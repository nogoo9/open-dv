"""Phase V2-6 M3 targeted live-provider smoke tests (`UAT-V26-E2E-04`): real agy & claude-cli."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from typer.testing import CliRunner

from opendv.agents.subprocess_runner import (
    build_agy_command,
    build_claude_cli_command,
    resolve_cli_binary,
)
from opendv.cli.main import app as cli_app
from opendv.cli.mcp_stdio import MCP_TOOL_NAMES
from opendv.models import WorkspaceSettings
from opendv.models.wire_dtos_discovery import CitationResolveReq, ProjectInitReq
from tests.api._api_test_helpers import copy_public_am_wave1, wait_run_via_sse
from tests.cli._cli_test_helpers import run_loopback_api_server
from tests.parity._v2_parity_helpers import (
    FIXTURE_TIMESTAMP,
    MD_STEWARDSHIP_QUOTE,
    setup_isolated_env,
    snapshot_dir_mtimes,
)


def _cli_api_json(args: list[str], *, env: dict[str, str]) -> dict[str, Any]:
    res = CliRunner().invoke(cli_app, ["--json", *args], env=env)
    assert res.exit_code == 0, f"CLI {args} exit={res.exit_code}: {res.output}"
    parsed: dict[str, Any] = json.loads(res.stdout.strip() or "{}")
    return parsed


@pytest.mark.live_provider
@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["agy", "claude-cli"])
async def test_uat_v26_e2e_04_live_provider_agy_and_claude_cli_smoke(
    provider: Literal["agy", "claude-cli"], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UAT-V26-E2E-04: Real agy (--print=<prompt>) & claude-cli (--tools='') over :8420, CLI & MCP."""
    env = setup_isolated_env(tmp_path / provider, monkeypatch, preserve_live_auth=True)
    host_before = snapshot_dir_mtimes(env.host_local_opendv)

    binary = resolve_cli_binary(provider)
    assert binary, f"Live provider binary not found for {provider}"
    if provider == "agy":
        cmd, stdin_txt = build_agy_command(binary, model_id="gemini-2.5-flash", prompt="smoke-check")
        assert "--print=smoke-check" in cmd and stdin_txt == ""
    else:
        cmd, _ = build_claude_cli_command(
            binary,
            model_id="claude-sonnet-4-5",
            step="step4",
            mcp_config_path=str(tmp_path / "mcp.json"),
            prompt="smoke-check",
        )
        assert "--tools" in cmd and cmd[cmd.index("--tools") + 1] == ""

    svc = env.make_service(harness=None)
    svc.update_settings(
        WorkspaceSettings(
            harness_provider=provider,
            secret_backend="user_state_file",
            cegar_policy="fast",
            updated_at=FIXTURE_TIMESTAMP,
        )
    )
    canary_secret = f"LIVE-SECRET-CANARY-{provider}-DO-NOT-LEAK"
    svc.set_secret("LIVE_SMOKE_CANARY", canary_secret)

    pub_dir = copy_public_am_wave1(env.ws)
    pid = f"am-live-{provider}"
    svc.init_project(ProjectInitReq(project_id=pid, domain_namespace="asset_management"))
    cli_env = env.subprocess_env()

    with run_loopback_api_server(svc) as base_url:
        async with httpx.AsyncClient(base_url=base_url, timeout=300.0) as client:
            # 1. Attach minimal real public_am slice via :8420 REST and inspect via opendv --api-url
            r_sql = await client.post(
                f"/api/v1/projects/{pid}/sources",
                json={
                    "kind": "sqlite",
                    "uri_or_filename": str(pub_dir / "am_sec_treasury_ibor.sqlite"),
                    "selected_tables": ["institutional_filers"],
                },
            )
            assert r_sql.status_code == 200, r_sql.text
            r_md = await client.post(
                f"/api/v1/projects/{pid}/sources",
                json={
                    "kind": "markdown",
                    "uri_or_filename": str(pub_dir / "am_stewardship_domain_notes.md"),
                },
            )
            assert r_md.status_code == 200, r_md.text
            sqlite_sid = r_sql.json()["source_id"]
            md_sid = r_md.json()["source_id"]

            cli_srcs = _cli_api_json(
                ["--api-url", base_url, "source", "list", "--project", pid], env=cli_env
            )
            assert len(cli_srcs["data"]) == 2

            # 2. Step 4 live discovery via :8420 HTTP + SSE (non-autonomous Graph A invariant)
            s4_start = await client.post(
                f"/api/v1/projects/{pid}/ontology/construct",
                json={
                    "discovery_mode": "incremental_new",
                    "hint": "Model InstitutionalFiler (institutional_filers.filer_cik) and StewardshipEngagementCase concisely.",
                },
            )
            assert s4_start.status_code in (200, 202), s4_start.text
            s4_rid = s4_start.json()["run_id"]
            sse4 = await wait_run_via_sse(
                client, f"/api/v1/projects/{pid}/ontology/runs/{s4_rid}/events"
            )
            assert len(sse4) > 0

            s4_state = (await client.get(f"/api/v1/projects/{pid}/ontology/runs/{s4_rid}")).json()
            for _ in range(3):
                if s4_state["status"] != "HOLD_CHECKPOINTED":
                    break
                answers = {
                    q["question_id"]: (q.get("recommended_answer") or "filer_cik")
                    for q in s4_state.get("pending_questions", [])
                }
                await client.post(
                    f"/api/v1/projects/{pid}/ontology/runs/{s4_rid}/answers",
                    json={"answers": answers},
                )
                await wait_run_via_sse(
                    client, f"/api/v1/projects/{pid}/ontology/runs/{s4_rid}/events"
                )
                s4_state = (await client.get(f"/api/v1/projects/{pid}/ontology/runs/{s4_rid}")).json()
            assert s4_state["status"] == "COMPLETED", (
                f"{s4_state} logs={(await client.get(f'/api/v1/projects/{pid}/runs/{s4_rid}/logs')).text}"
            )
            assert s4_state["budget_turns_used"] >= 1

            ont_before = (await client.get(f"/api/v1/projects/{pid}/ontology")).json()
            assert ont_before["ontology"]["concepts"] == []

            # 3. Human steward commits grounded ontology -> Step 6 live CEGAR -> Step 7 DuckDB
            cite_md = svc.resolve_citation_span(
                pid,
                CitationResolveReq(
                    doc_id=md_sid, page_number=1, verbatim_quote=MD_STEWARDSHIP_QUOTE, occurrence=1
                ),
            )
            proj_before = svc.get_project(pid)
            commit_resp = await client.post(
                f"/api/v1/projects/{pid}/ontology/commit",
                json={
                    "base_revision_id": proj_before.active_ontology_rev_id or "",
                    "rationale": f"Live {provider} smoke grounded InstitutionalFiler commit",
                    "low_confidence_grounding_waiver": True,
                    "grounding_rationale": "Grounded against public_am SQLite and stewardship notes",
                    "waive_quarantine": True,
                    "operations": [
                        {
                            "op_id": "op_live_filer",
                            "op_type": "AddConcept",
                            "concept_uri": "urn:asset_mgmt:InstitutionalFiler",
                            "concept_name": "InstitutionalFiler",
                            "description": "SEC 13F institutional investment manager keyed by filer_cik.",
                            "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                            "table_bindings": [
                                {
                                    "source_id": sqlite_sid,
                                    "table_id": f"{sqlite_sid}:institutional_filers",
                                    "col_name": "filer_cik",
                                }
                            ],
                            "source_citations": [
                                {
                                    "doc_id": md_sid,
                                    "page_number": 1,
                                    "verbatim_quote": MD_STEWARDSHIP_QUOTE,
                                    "occurrence": 1,
                                    "char_start": cite_md.char_start,
                                    "char_end": cite_md.char_end,
                                    "content_sha256": cite_md.resolved_sha256,
                                }
                            ],
                            "rationale": "Bind InstitutionalFiler to institutional_filers.filer_cik",
                        }
                    ],
                },
            )
            assert commit_resp.status_code == 200, commit_resp.text

            s6_start = await client.post(f"/api/v1/projects/{pid}/runs", json={"policy": "fast"})
            assert s6_start.status_code in (200, 202), s6_start.text
            s6_rid = s6_start.json()["run_id"]
            await wait_run_via_sse(client, f"/api/v1/projects/{pid}/runs/{s6_rid}/events")
            s6_state = (await client.get(f"/api/v1/projects/{pid}/runs/{s6_rid}")).json()
            for _ in range(3):
                if s6_state["status"] != "HOLD_CHECKPOINTED":
                    break
                answers6 = {
                    q["question_id"]: (
                        q.get("recommended_answer")
                        or (q["options"][0]["option_id"] if q.get("options") else "filer_cik")
                    )
                    for q in s6_state.get("pending_questions", [])
                }
                await client.post(
                    f"/api/v1/projects/{pid}/runs/{s6_rid}/decisions",
                    json={"answers": answers6},
                )
                await wait_run_via_sse(client, f"/api/v1/projects/{pid}/runs/{s6_rid}/events")
                s6_state = (await client.get(f"/api/v1/projects/{pid}/runs/{s6_rid}")).json()
            assert s6_state["status"] in {"COMPLETED", "CERTIFIED_FAST"}, (
                f"{s6_state} logs={(await client.get(f'/api/v1/projects/{pid}/runs/{s6_rid}/logs')).text}"
            )

            ir_resp = await client.get(f"/api/v1/projects/{pid}/vault/ir")
            assert ir_resp.status_code == 200, ir_resp.text
            ir_data = ir_resp.json()
            assert len(ir_data["hubs"]) >= 1

            # 4. Step 7 compile/migrate via opendv --api-url & query via opendv mcp serve
            _cli_api_json(
                ["--api-url", base_url, "storage", "compile", "--project", pid, "--target", "duckdb_local"],
                env=cli_env,
            )
            mig = _cli_api_json(
                ["--api-url", base_url, "storage", "migrate", "--project", pid, "--target", "duckdb_local"],
                env=cli_env,
            )
            assert mig["data"]["verified"] is True

            mcp_params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "opendv.cli", "mcp", "serve", "--transport", "stdio", "--project", pid],
                env=cli_env,
            )
            async with stdio_client(mcp_params) as (r_stream, w_stream), ClientSession(
                r_stream, w_stream
            ) as mcp:
                await mcp.initialize()
                tools = await mcp.list_tools()
                assert {t.name for t in tools.tools} == set(MCP_TOOL_NAMES)
                q_res = await mcp.call_tool(
                    "storage_query",
                    {
                        "sql": (
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'raw_vault' ORDER BY 1"
                        ),
                        "limit": 20,
                    },
                )
                assert not q_res.is_error
                q_payload = json.loads(getattr(q_res.content[0], "text", "{}") if q_res.content else "{}")
                assert len(q_payload["rows"]) >= 1

            logs_txt = (await client.get(f"/api/v1/projects/{pid}/runs/{s6_rid}/logs")).text
            assert canary_secret not in logs_txt

    assert snapshot_dir_mtimes(env.host_local_opendv) == host_before
