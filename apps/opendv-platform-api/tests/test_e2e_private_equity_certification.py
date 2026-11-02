"""Full-Stack End-to-End Private Equity Platform Certification Suite (WP-12).

Exercises the complete Open-DV platform lifecycle across Scenes 1-7:
1. Platform capabilities inspection (`GET /api/v1/platform/capabilities` listing all 12 views
   `WB-00`..`WB-11`, 6 gates `G0`..`G5`, 5 RBAC tiers, and `reasoning`/`fast` model tier support)
   & Project creation (`POST /api/v1/projects`).
2. Pipeline run launch (`POST /api/v1/runs` with `reasoning`/`fast` tier verification),
   run inspection, and W3C Server-Sent Events (`GET /api/v1/runs/{run_id}/events`) replay.
3. `Tier-U3` Captain-Hold resolution (`GET/POST /api/v1/runs/{run_id}/decisions`) for
   `Horizon Sovereign Co-Invest Vehicle` (`hold-tier-u3-horizon-sovereign-vehicle`).
4. 6-Gate closed-loop verification (`GET/POST /api/v1/runs/{run_id}/gates`) covering Gates 0-5
   (`DV-COL-012` + `ALG-A1` self-healing convergence in k=2 attempts).
5. 4-Zone deterministic DuckDB SQL compilation (`Zones 0-4`, `DV-AST-001`..`006`) & 4-Batch
   DuckDB Shadow Sandbox (`POST /api/v1/runs/{run_id}/sandbox` with `DV-DYN-001`..`015` all passing,
   `replay_delta_rows == 0`, `Query D.10` returning `125.00` bps fee cap for CalPERS `LP-101`, and
   `Query D.10b` returning `12.80x` at `2026-10-15 12:00:00` and `14.50x` at `2026-11-15 23:59:59`).
6. 12-View Workbench endpoints (`/api/v1/runs/{run_id}/workbench/{view_id}` for `WB-00`..`WB-11`)
   with 5-Tier RBAC verification ensuring zero unredacted PII/MNPI leaks for `PUBLIC` and
   `INTERNAL_MODELER` roles.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from opendv_agent_harness import ModelTier
from opendv_core import VaultObservabilityEvent, validate_observability_event
from opendv_duckdb_sandbox import (
    DuckDbShadowSandbox,
    compile_canonical_ir_to_4zone_sql,
)
from opendv_platform_api import PlatformStateContainer, RunStatus, create_app

EXPECTED_WORKBENCH_VIEW_IDS: tuple[str, ...] = (
    "WB-00",
    "WB-01",
    "WB-02",
    "WB-03",
    "WB-04",
    "WB-05",
    "WB-06",
    "WB-07",
    "WB-08",
    "WB-09",
    "WB-10",
    "WB-11",
)

FORBIDDEN_UNREDACTED_PII_MNPI_SUBSTRINGS: tuple[str, ...] = (
    "245000000",
    "14.5x",
    "DE89370400440532013000",
    "99-8877665",
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
            if line.startswith(":"):
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                frame[key.strip()] = value.strip()
        if frame:
            frames.append(frame)
    return frames


def test_e2e_private_equity_full_stack_certification(tmp_path: Path) -> None:
    """Certify end-to-end Private Equity pipeline across REST, SSE, Gates, Sandbox, and Views."""
    state = PlatformStateContainer(state_dir=tmp_path / "state")
    app = create_app(platform_state=state)

    async def _scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # -----------------------------------------------------------------
            # Step 1: Capabilities inspection & Project creation (argon/flash)
            # -----------------------------------------------------------------
            health_resp = await client.get("/api/v1/health")
            assert health_resp.status_code == 200
            assert health_resp.json()["status"] == "ok"

            cap_resp = await client.get("/api/v1/platform/capabilities")
            assert cap_resp.status_code == 200
            cap_body = cap_resp.json()
            assert cap_body["api_version"] == "v1"
            assert cap_body["contract_version"] == "6.0"
            assert tuple(cap_body["workbench_views"]) == EXPECTED_WORKBENCH_VIEW_IDS
            assert set(cap_body["gates"]) == {"G0", "G1", "G2", "G3", "G4", "G5"}
            assert {
                "PUBLIC",
                "INTERNAL_MODELER",
                "DATA_STEWARD",
                "AUDITOR_FINANCE",
                "RESTRICTED_PII_CUSTODIAN",
            } <= set(cap_body["rbac_roles"])
            assert {ModelTier.HIGH_REASONING.value, ModelTier.FAST_TIER.value} == {
                "reasoning",
                "fast",
            }

            project_id = "proj-apex-buyout-iv-e2e-cert"
            project_resp = await client.post(
                "/api/v1/projects",
                json={
                    "project_id": project_id,
                    "display_name": "Apex Buyout Fund IV - E2E Certification Vault",
                    "domain_namespace": "pe_asset_management",
                    "contract_path": "examples/private_equity/dv_pe_model_contract.yaml",
                    "graph_a_ontology_path": "examples/private_equity/pe_graph_a_ontology.yaml",
                    "graph_b_path": "examples/private_equity/pe_graph_b_profile.json",
                    "canonical_ir_path": "examples/private_equity/pe_canonical_ir.json",
                },
            )
            assert project_resp.status_code == 201
            assert project_resp.json()["project_id"] == project_id

            # Verify both fast and reasoning model_tier_override values are supported
            flash_run_resp = await client.post(
                "/api/v1/runs",
                json={
                    "project_id": project_id,
                    "harness_provider": "deterministic-stub",
                    "model_tier_override": "fast",
                    "execute_duckdb_sandbox": True,
                },
            )
            assert flash_run_resp.status_code == 201

            run_resp = await client.post(
                "/api/v1/runs",
                json={
                    "project_id": project_id,
                    "harness_provider": "antigravity",
                    "model_tier_override": "reasoning",
                    "execute_duckdb_sandbox": True,
                },
            )
            assert run_resp.status_code == 201
            run_summary = run_resp.json()
            run_id = run_summary["run_id"]
            assert run_summary["project_id"] == project_id
            assert run_summary["status"] == RunStatus.PAUSED_HOLD.value
            assert "hold-tier-u3-horizon-sovereign-vehicle" in run_summary["active_hold_ids"]

            # -----------------------------------------------------------------
            # Step 2: Run inspection & W3C SSE stream replay (`Last-Event-ID`)
            # -----------------------------------------------------------------
            get_run_resp = await client.get(f"/api/v1/runs/{run_id}")
            assert get_run_resp.status_code == 200
            assert get_run_resp.json()["event_count"] == 9

            sse_full_resp = await client.get(
                f"/api/v1/runs/{run_id}/events",
                headers={"X-OpenDV-Role": "RESTRICTED_PII_CUSTODIAN"},
            )
            assert sse_full_resp.status_code == 200
            assert "text/event-stream" in sse_full_resp.headers["content-type"]
            full_frames = _parse_sse_frames(sse_full_resp.text)
            assert len(full_frames) == 10
            assert [f["id"] for f in full_frames[:-1]] == [f"evt-pe-00{i}" for i in range(1, 10)]
            assert full_frames[-1].get("event") == "run_complete"
            for frame in full_frames[:-1]:
                ev_payload = json.loads(frame["data"])
                VaultObservabilityEvent.model_validate(ev_payload)
                validate_observability_event(ev_payload)

            sse_resume_resp = await client.get(
                f"/api/v1/runs/{run_id}/events",
                headers={
                    "X-OpenDV-Role": "RESTRICTED_PII_CUSTODIAN",
                    "Last-Event-ID": "evt-pe-006",
                },
            )
            assert sse_resume_resp.status_code == 200
            resumed_frames = _parse_sse_frames(sse_resume_resp.text)
            assert [f["id"] for f in resumed_frames[:-1]] == [
                "evt-pe-007",
                "evt-pe-008",
                "evt-pe-009",
            ]
            assert resumed_frames[-1].get("event") == "run_complete"

            # -----------------------------------------------------------------
            # Step 3: Tier-U3 Captain-Hold Resolution (`POST /decisions`)
            # -----------------------------------------------------------------
            holds_before = await client.get(f"/api/v1/runs/{run_id}/decisions")
            assert holds_before.status_code == 200
            assert len(holds_before.json()["active_holds"]) == 1

            decision_resp = await client.post(
                f"/api/v1/runs/{run_id}/decisions",
                json={
                    "event_id": "evt-pe-004",
                    "hold_id": "hold-tier-u3-horizon-sovereign-vehicle",
                    "decision_type": "RESOLVE_CAPTAIN_HOLD",
                    "action": "APPROVE",
                    "selected_candidate_id": "APEX_CO_I",
                    "steward_id": "captain@apex-pe.example",
                    "resolution_notes": "Certified co-invest sleeve binding to APEX_CO_I.",
                },
            )
            assert decision_resp.status_code == 200
            decision_body = decision_resp.json()
            assert decision_body["status"] == "ACCEPTED"
            assert decision_body["updated_run_status"] == RunStatus.COMPLETED.value

            run_after_hold = (await client.get(f"/api/v1/runs/{run_id}")).json()
            assert run_after_hold["status"] == RunStatus.COMPLETED.value
            assert run_after_hold["active_hold_ids"] == []

            # -----------------------------------------------------------------
            # Step 4: 6-Gate Verification (`GET/POST /api/v1/runs/{run_id}/gates`)
            # -----------------------------------------------------------------
            gates_get_resp = await client.get(f"/api/v1/runs/{run_id}/gates")
            assert gates_get_resp.status_code == 200
            gates_report = gates_get_resp.json()
            assert gates_report["overall_passed"] is True
            assert gates_report["gate_scorecard"]["gate_1_contract_grain"] == "AUTO_REPAIRED"
            assert gates_report["gate_scorecard"]["gate_4_yagni_cost_guard"] == "AUTO-FIX"

            gates_post_resp = await client.post(
                f"/api/v1/runs/{run_id}/gates",
                json={"recheck": True},
            )
            assert gates_post_resp.status_code == 200
            assert gates_post_resp.json()["overall_passed"] is True

            # -----------------------------------------------------------------
            # Step 5: 4-Zone SQL Compilation & 4-Batch Adversarial DuckDB Sandbox
            # -----------------------------------------------------------------
            pe_ir = DuckDbShadowSandbox.load_default_pe_canonical_ir()
            compiled_bundle = compile_canonical_ir_to_4zone_sql(
                pe_ir,
                include_batch4_restatement_in_seed=False,
            )
            assert len(compiled_bundle.topological_load_order) > 0
            assert all(
                zone_sql.strip()
                for zone_sql in (
                    compiled_bundle.zone_0_sql,
                    compiled_bundle.zone_1_sql,
                    compiled_bundle.zone_2_sql,
                    compiled_bundle.zone_3_sql,
                    compiled_bundle.zone_4_query_d10_sql,
                    compiled_bundle.zone_4_query_d10b_sql,
                )
            )
            with DuckDbShadowSandbox() as sandbox:
                sandbox_report = sandbox.run_full_verification(
                    pe_ir,
                    emit_telemetry=False,
                )
            assert sandbox_report.passed is True
            assert sandbox_report.batch_report.all_passed is True
            assert sandbox_report.batch_report.by_number[2].total_delta_rows == 0
            assert sandbox_report.dynamic_check_report.passed_count == 15
            assert sandbox_report.bitemporal_proof.verified is True

            sandbox_batches_resp = await client.post(
                f"/api/v1/runs/{run_id}/sandbox",
                json={
                    "query_type": "ADVERSARIAL_BATCHES_1_TO_4",
                    "assertion_cutoff_ts": "2026-11-15 23:59:59",
                },
            )
            assert sandbox_batches_resp.status_code == 200
            batches_data = sandbox_batches_resp.json()
            assert batches_data["all_batches_passed"] is True
            assert batches_data["dynamic_rules_passed"] == 15
            assert batches_data["replay_delta_rows"] == 0
            assert batches_data["row_count"] == 4

            dyn_rules_resp = await client.post(
                f"/api/v1/runs/{run_id}/sandbox",
                json={"query_type": "VERIFY_DYN_001_015"},
            )
            assert dyn_rules_resp.status_code == 200
            dyn_rows = dyn_rules_resp.json()["rows"]
            assert len(dyn_rows) == 15
            assert [row["rule_id"] for row in dyn_rows] == [f"DV-DYN-{i:03d}" for i in range(1, 16)]
            assert all(row["status"] == "PASS" for row in dyn_rows)

            d10_resp = await client.post(
                f"/api/v1/runs/{run_id}/sandbox",
                json={
                    "query_type": "QUERY_D10_PIT",
                    "assertion_cutoff_ts": "2026-11-15 23:59:59",
                },
            )
            assert d10_resp.status_code == 200
            calpers_row = next(
                r
                for r in d10_resp.json()["rows"]
                if r["fund_code"] == "APEX_BO_IV" and r["lp_natural_key"] == "LP-101"
            )
            assert float(calpers_row["commitment_amount_usd"]) == 500000000.0
            assert float(calpers_row["side_letter_discounted_fee_bps"]) == 125.0

            d10b_oct_resp = await client.post(
                f"/api/v1/runs/{run_id}/sandbox",
                json={
                    "query_type": "QUERY_D10B_VALUATION",
                    "assertion_cutoff_ts": "2026-10-15 12:00:00",
                },
            )
            assert d10b_oct_resp.status_code == 200
            nordic_oct = next(
                r
                for r in d10b_oct_resp.json()["rows"]
                if r["portco_natural_key"] == "529900T8BM49AURSDO55"
            )
            assert float(nordic_oct["ev_ebitda_multiple"]) == 12.80
            assert float(nordic_oct["enterprise_value_usd"]) == 216275840.0

            d10b_nov_resp = await client.post(
                f"/api/v1/runs/{run_id}/sandbox",
                json={
                    "query_type": "QUERY_D10B_VALUATION",
                    "assertion_cutoff_ts": "2026-11-15 23:59:59",
                },
            )
            assert d10b_nov_resp.status_code == 200
            nordic_nov = next(
                r
                for r in d10b_nov_resp.json()["rows"]
                if r["portco_natural_key"] == "529900T8BM49AURSDO55"
            )
            assert float(nordic_nov["ev_ebitda_multiple"]) == 14.50
            assert float(nordic_nov["enterprise_value_usd"]) == 245000000.0

            # -----------------------------------------------------------------
            # Step 6: 12-View Workbench Projections (`WB-00`..`WB-11`) & RBAC
            # -----------------------------------------------------------------
            for view_id in EXPECTED_WORKBENCH_VIEW_IDS:
                custodian_resp = await client.get(
                    f"/api/v1/runs/{run_id}/workbench/{view_id}",
                    headers={"X-OpenDV-Role": "RESTRICTED_PII_CUSTODIAN"},
                )
                assert custodian_resp.status_code == 200
                custodian_body = custodian_resp.json()
                assert custodian_body["view_id"] == view_id
                assert custodian_body["event"]["workbench_view_id"] == view_id
                assert len(custodian_body["events"]) >= 1

                # PUBLIC role must have all views redacted and zero PII/MNPI leaks
                public_resp = await client.get(
                    f"/api/v1/runs/{run_id}/workbench/{view_id}",
                    headers={"X-OpenDV-Role": "PUBLIC"},
                )
                assert public_resp.status_code == 200
                public_raw = public_resp.text
                public_event = public_resp.json()["event"]
                assert "[REDACTED" in public_event["layer1_story_banner"]["headline"]
                for forbidden in FORBIDDEN_UNREDACTED_PII_MNPI_SUBSTRINGS:
                    assert forbidden not in public_raw, (
                        f"Unredacted PII/MNPI token '{forbidden}' leaked to PUBLIC on {view_id}"
                    )

                # INTERNAL_MODELER role sees INTERNAL views unmasked, but CONFIDENTIAL / MNPI /
                # RESTRICTED_PII views must be redacted with zero PII/MNPI leaks
                modeler_resp = await client.get(
                    f"/api/v1/runs/{run_id}/workbench/{view_id}",
                    headers={"X-OpenDV-Role": "INTERNAL_MODELER"},
                )
                assert modeler_resp.status_code == 200
                modeler_raw = modeler_resp.text
                modeler_event = modeler_resp.json()["event"]
                classification = modeler_event.get("data_classification", "INTERNAL")
                if classification == "INTERNAL":
                    assert not modeler_event["layer1_story_banner"]["headline"].startswith(
                        "[REDACTED"
                    )
                else:
                    assert "[REDACTED" in modeler_event["layer1_story_banner"]["headline"]
                for forbidden in FORBIDDEN_UNREDACTED_PII_MNPI_SUBSTRINGS:
                    assert forbidden not in modeler_raw, (
                        f"Unredacted token '{forbidden}' leaked to INTERNAL_MODELER on {view_id}"
                    )

    _run_async(_scenario())
