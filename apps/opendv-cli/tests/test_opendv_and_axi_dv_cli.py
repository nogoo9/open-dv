"""Integration tests for `opendv` and `axi-dv` CLI commands in `--local` and `--api-url` modes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from opendv_cli import (
    OpenDVCliApiClient,
    OpenDVCliApiError,
    app_main,
    axi_dv_main,
    validate_api_url,
)
from opendv_platform_api import PlatformStateContainer, create_app
from opendv_platform_api.deps import REPO_ROOT


class _AsgiSyncBridgeTransport(httpx.BaseTransport):
    """Bridge synchronous `httpx.Client` requests to an in-process FastAPI ASGI application."""

    def __init__(self, app: Any) -> None:
        self._app = app

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        async def _send() -> httpx.Response:
            async_transport = httpx.ASGITransport(app=self._app)
            async with httpx.AsyncClient(
                transport=async_transport, base_url="http://testserver"
            ) as client:
                async_res = await client.request(
                    method=request.method,
                    url=str(request.url),
                    headers=dict(request.headers),
                    content=request.read(),
                )
                return httpx.Response(
                    status_code=async_res.status_code,
                    headers=async_res.headers,
                    content=async_res.content,
                    request=request,
                )

        return asyncio.run(_send())


def test_opendv_cli_local_subcommands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify `opendv` subcommands (`verify`, `profile`, `compile`, `sandbox`, `run`, `hold`)."""
    # 1. verify --local --json
    rc = app_main(["verify", "--local", "--json"])
    assert rc == 0
    out_verify = json.loads(capsys.readouterr().out)
    assert out_verify["mode"] == "local"
    assert out_verify["overall_passed"] is True
    assert "@toon_v1" in out_verify["toon_text"]

    # 2. axi-dv default verify with --toon and option value matching subcommand name
    rc_toon = axi_dv_main(["--local", "--toon"])
    assert rc_toon == 0
    out_toon = capsys.readouterr().out.strip()
    assert out_toon.startswith("@toon_v1")

    rc_opt_sub = axi_dv_main(["--run-id", "hold", "--local", "--json"])
    assert rc_opt_sub == 0
    out_opt_sub = json.loads(capsys.readouterr().out)
    assert out_opt_sub["run_id"] == "hold"
    assert out_opt_sub["overall_passed"] is True

    rc_default_opendv = app_main(["--local", "--json"])
    assert rc_default_opendv == 0
    out_default_opendv = json.loads(capsys.readouterr().out)
    assert out_default_opendv["overall_passed"] is True

    # 3. profile --local --json
    rc_prof = app_main(["profile", "--local", "--json"])
    assert rc_prof == 0
    out_prof = json.loads(capsys.readouterr().out)
    assert out_prof["mode"] == "local"
    assert out_prof["status"] == "PASS"
    assert out_prof["table_count"] >= 5

    # 4. compile --local --json
    rc_comp = app_main(["compile", "--local", "--json"])
    assert rc_comp == 0
    out_comp = json.loads(capsys.readouterr().out)
    assert out_comp["mode"] == "local"
    assert out_comp["status"] == "PASS"
    assert out_comp["hubs_compiled"] == 4
    assert out_comp["links_compiled"] == 5
    assert out_comp["satellites_compiled"] == 11

    # 5. sandbox --local --json
    rc_sbx = app_main(
        [
            "sandbox",
            "--local",
            "--query-type",
            "ADVERSARIAL_BATCHES_1_TO_4",
            "--cutoff",
            "2026-11-15 23:59:59",
            "--json",
        ]
    )
    assert rc_sbx == 0
    out_sbx = json.loads(capsys.readouterr().out)
    assert out_sbx["all_batches_passed"] is True
    assert out_sbx["dynamic_rules_passed"] == 15
    assert len(out_sbx["query_d10b_rows"]) == 3

    # 6. run --local --json (get, list filter, events with RBAC redaction & cursor, missing run_id)
    rc_run = app_main(["run", "get", "--local", "--json"])
    assert rc_run == 0
    out_run = json.loads(capsys.readouterr().out)
    assert out_run["run_id"] == "run-pe-apex-horizon-20261030"

    rc_list_filtered = app_main(
        ["run", "list", "--local", "--project-id", "proj-nonexistent", "--json"]
    )
    assert rc_list_filtered == 0
    out_list_filtered = json.loads(capsys.readouterr().out)
    assert out_list_filtered["total"] == 0

    rc_ev = app_main(["run", "events", "--local", "--role", "PUBLIC", "--json"])
    assert rc_ev == 0
    out_ev = json.loads(capsys.readouterr().out)
    assert out_ev["event_count"] == 9
    assert out_ev["events"][0]["layer1_story_banner"]["headline"].startswith("[REDACTED ")

    rc_ev_cursor = app_main(
        [
            "run",
            "events",
            "--local",
            "--last-event-id",
            "evt-pe-005",
            "--json",
        ]
    )
    assert rc_ev_cursor == 0
    out_ev_cursor = json.loads(capsys.readouterr().out)
    assert out_ev_cursor["event_count"] == 4
    assert out_ev_cursor["events"][0]["event_id"] == "evt-pe-006"

    rc_abort_missing = app_main(["run", "abort", "--local", "--run-id", "run-missing", "--json"])
    assert rc_abort_missing == 1
    out_abort_missing = json.loads(capsys.readouterr().out)
    assert out_abort_missing["detail"] == "Run 'run-missing' not found."

    # 7. hold --local --json (list, approve, & missing run_id)
    rc_hold_list = app_main(["hold", "list", "--local", "--json"])
    assert rc_hold_list == 0
    out_hold_list = json.loads(capsys.readouterr().out)
    assert len(out_hold_list["active_holds"]) == 1

    rc_hold_missing = app_main(["hold", "approve", "--local", "--run-id", "run-missing", "--json"])
    assert rc_hold_missing == 1
    out_hold_missing = json.loads(capsys.readouterr().out)
    assert out_hold_missing["detail"] == "Run 'run-missing' not found."

    rc_hold_approve = app_main(
        [
            "hold",
            "approve",
            "--local",
            "--candidate",
            "APEX_CO_I",
            "--notes",
            "Approved via local CLI test.",
            "--json",
        ]
    )
    assert rc_hold_approve == 0
    out_hold_approve = json.loads(capsys.readouterr().out)
    assert out_hold_approve["status"] == "ACCEPTED"
    assert out_hold_approve["updated_run_status"] == "COMPLETED"


def test_opendv_and_axi_dv_cli_remote_api_url_mode(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify `opendv` and `axi-dv` subcommands in `--api-url` mode against the FastAPI ASGI app."""
    state = PlatformStateContainer(state_dir=tmp_path / "state")
    app = create_app(platform_state=state)
    transport = _AsgiSyncBridgeTransport(app)
    api_url = "http://testserver"

    # 1. axi-dv verify --api-url
    rc_verify = axi_dv_main(
        ["verify", "--api-url", api_url, "--json"],
        transport=transport,
    )
    assert rc_verify == 0
    out_verify = json.loads(capsys.readouterr().out)
    assert out_verify["overall_passed"] is True

    # 2. opendv profile --api-url
    rc_prof = app_main(
        ["profile", "--api-url", api_url, "--json"],
        transport=transport,
    )
    assert rc_prof == 0
    out_prof = json.loads(capsys.readouterr().out)
    assert out_prof["mode"] == "remote"
    assert out_prof["workbench_view"]["view_id"] == "WB-02"

    # 3. opendv compile --api-url
    rc_comp = app_main(
        ["compile", "--api-url", api_url, "--json"],
        transport=transport,
    )
    assert rc_comp == 0
    out_comp = json.loads(capsys.readouterr().out)
    assert out_comp["mode"] == "remote"
    assert out_comp["workbench_view"]["view_id"] == "WB-11"

    # 4. opendv sandbox --api-url (QUERY_D10B_VALUATION)
    rc_sbx = app_main(
        [
            "sandbox",
            "--api-url",
            api_url,
            "--query-type",
            "QUERY_D10B_VALUATION",
            "--cutoff",
            "2026-11-15 23:59:59",
            "--json",
        ],
        transport=transport,
    )
    assert rc_sbx == 0
    out_sbx = json.loads(capsys.readouterr().out)
    assert out_sbx["all_batches_passed"] is True
    nordic = next(r for r in out_sbx["rows"] if r["portco_natural_key"] == "529900T8BM49AURSDO55")
    assert float(nordic["ev_ebitda_multiple"]) == 14.50

    # 5. opendv run events --api-url with Last-Event-ID
    rc_events = app_main(
        [
            "run",
            "events",
            "--api-url",
            api_url,
            "--role",
            "AUDITOR_FINANCE",
            "--last-event-id",
            "evt-pe-007",
            "--json",
        ],
        transport=transport,
    )
    assert rc_events == 0
    out_events = json.loads(capsys.readouterr().out)
    assert "id: evt-pe-008" in out_events["sse_stream"]
    assert "id: evt-pe-009" in out_events["sse_stream"]
    assert "id: evt-pe-001" not in out_events["sse_stream"]
    assert "event: run_complete" in out_events["sse_stream"]

    # 6. opendv hold approve --api-url
    rc_hold = app_main(
        [
            "hold",
            "approve",
            "--api-url",
            api_url,
            "--candidate",
            "APEX_CO_I",
            "--notes",
            "Approved via remote CLI.",
            "--json",
        ],
        transport=transport,
    )
    assert rc_hold == 0
    out_hold = json.loads(capsys.readouterr().out)
    assert out_hold["status"] == "ACCEPTED"
    assert out_hold["updated_run_status"] == "COMPLETED"

    # 7. Double-approving the same hold over --api-url returns exit code 1 with HTTP 409 error
    rc_conflict = app_main(
        ["hold", "approve", "--api-url", api_url, "--json"],
        transport=transport,
    )
    assert rc_conflict == 1
    out_conflict = json.loads(capsys.readouterr().out)
    assert out_conflict["status"] == "ERROR"
    assert out_conflict["status_code"] == 409


def test_api_client_url_validation_and_methods(tmp_path: Path) -> None:
    """Verify `validate_api_url` rejects non-HTTP schemes and `OpenDVCliApiClient` methods work."""
    with pytest.raises(ValueError, match="Invalid --api-url"):
        validate_api_url("file:///etc/passwd")

    state = PlatformStateContainer(state_dir=tmp_path / "state")
    app = create_app(platform_state=state)
    transport = _AsgiSyncBridgeTransport(app)

    with OpenDVCliApiClient(
        base_url="http://testserver",
        role="RESTRICTED_PII_CUSTODIAN",
        transport=transport,
    ) as client:
        assert client.get_health()["status"] == "ok"
        assert "antigravity" in client.get_capabilities()["harness_providers"]
        assert client.list_projects()["total"] >= 1
        assert client.get_project("proj-apex-horizon-pe")["project_id"] == "proj-apex-horizon-pe"
        assert client.list_runs()["total"] >= 1
        assert client.get_gates("run-pe-apex-horizon-20261030")["overall_passed"] is True
        assert client.get_sandbox("run-pe-apex-horizon-20261030")["run_id"] == (
            "run-pe-apex-horizon-20261030"
        )
        uploaded = client.upload_artifact({"content": {"cli": True}})
        fetched = client.get_artifact(uploaded["sha256"])
        assert fetched["content"] == {"cli": True}

        with pytest.raises(OpenDVCliApiError) as exc_info:
            client.get_run("run-missing")
        assert exc_info.value.status_code == 404


def test_verify_cli_rejected_and_ask_user_contracts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify `opendv verify` and `axi-dv` reflect REJECT_WITH_TRACE and ASK_USER verdicts."""
    bad_contract = tmp_path / "broken_contract.yaml"
    bad_contract.write_text(
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
    rc_bad = app_main(["--local", "--contract", str(bad_contract), "--json"])
    assert rc_bad == 1
    out_bad = json.loads(capsys.readouterr().out)
    assert out_bad["overall_passed"] is False
    assert out_bad["verdict"] == "REJECT-WITH-TRACE"
    assert out_bad["gate_scorecard"]["gate_1_contract_grain"] == "REJECT-WITH-TRACE"

    pe_contract_doc = yaml.safe_load(
        (REPO_ROOT / "examples" / "private_equity" / "dv_pe_model_contract.yaml").read_text(
            encoding="utf-8"
        )
    )
    pe_contract_doc["unmapped_escalations"] = [
        {
            "bronze_table": "src_mystery",
            "columns": ["col_a"],
            "escalation_reason": "MISSING_ONTOLOGY_CONCEPT",
            "architect_question": "Map src_mystery to which hub?",
        }
    ]
    ask_contract = tmp_path / "ask_contract.yaml"
    ask_contract.write_text(yaml.safe_dump(pe_contract_doc), encoding="utf-8")
    rc_ask = axi_dv_main(["--local", "--contract", str(ask_contract), "--json"])
    assert rc_ask == 0
    out_ask = json.loads(capsys.readouterr().out)
    assert out_ask["overall_passed"] is True
    assert out_ask["verdict"] == "ASK-USER"
    assert out_ask["gate_scorecard"]["gate_1_contract_grain"] == "ASK-USER"
