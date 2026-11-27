"""Comprehensive zero-skip HTTP/SSE UAT suite for opendv.api (UAT-V26-API-01..06)."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from opendv.agents import Tier2ProbeOutcome
from opendv.api import create_app, export_openapi_schema
from opendv.models.diagnostics import ClarificationQuestion, EmitterDefectReport
from opendv.models.errors import ERROR_MAPPINGS, ErrorCode, OpenDVError
from opendv.models.events import RunStreamEvent
from opendv.models.ontology import ConceptAttribute, ConceptTableBinding
from opendv.models.ontology_deltas import AddConcept
from opendv.models.wire_dtos_discovery import ProjectInitReq, SourceAttachReq
from opendv.models.wire_dtos_vault import HorizonCriticVerdict, SemanticCriticVerdict
from opendv.service.app_service import OpenDVService
from opendv.service.external_mcp import ExternalMcpGateway
from opendv.service.readiness import HarnessReadinessService
from opendv.service.secrets import KeyringSecretStore
from opendv.service.settings_store import SettingsStore
from opendv.service.state_store import ProjectStateManager
from tests.api._api_test_helpers import (
    build_wave1_am_draft,
    build_wave1_commit_ops,
    build_wave2_am_draft,
    build_wave2_commit_ops,
    copy_public_am_wave1,
    copy_public_am_wave2,
    load_project_profiles,
    wait_run_via_sse,
)
from tests.fixtures.scale_200 import build_scale_200_sqlite
from tests.helpers.archive_builder import ArchiveFixtureBuilder
from tests.helpers.fake_external_mcp import LoopbackFakeExternalMcpServer
from tests.helpers.mock_harness import DeterministicMockHarness, MockTurnScript


def _make_svc(tmp_path: Path, *, harness: DeterministicMockHarness | None = None) -> OpenDVService:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return OpenDVService(ws, state_dir=tmp_path / "state", harness=harness)


def _assert_problem(
    resp: httpx.Response | Any,
    *,
    status: int,
    error_code: str,
) -> dict[str, Any]:
    assert resp.status_code == status
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["status"] == status
    assert body["error_code"] == error_code
    for req_key in ("type", "title", "detail", "category", "waivable", "rationale_required"):
        assert req_key in body
    return body


@pytest.mark.asyncio
async def test_uat_v26_api_01_loopback_host_origin_sec_fetch_and_cors(tmp_path: Path) -> None:
    svc = _make_svc(tmp_path)
    app = create_app(svc)

    # Default TestClient (scope["server"] = ("testserver", 80)) passes
    with TestClient(app) as tc:
        r_tc = tc.get("/api/v1/health")
        assert r_tc.status_code == 200
        assert r_tc.headers.get("access-control-allow-origin") != "*"

    # ASGITransport with loopback 127.0.0.1:8420
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        for valid_host in ("127.0.0.1:8420", "localhost:8420", "[::1]:8420"):
            r = await client.get("/api/v1/health", headers={"Host": valid_host})
            assert r.status_code == 200
            assert "access-control-allow-origin" not in {k.lower() for k in r.headers}

        # testserver Host rejected when server scope is NOT testserver
        _assert_problem(
            await client.get("/api/v1/health", headers={"Host": "testserver"}),
            status=403,
            error_code="LOOPBACK_ORIGIN_FORBIDDEN",
        )
        _assert_problem(
            await client.get("/api/v1/health", headers={"Host": "evil.example:8420"}),
            status=403,
            error_code="LOOPBACK_ORIGIN_FORBIDDEN",
        )
        _assert_problem(
            await client.get("/api/v1/health", headers={"Origin": "http://evil.example"}),
            status=403,
            error_code="LOOPBACK_ORIGIN_FORBIDDEN",
        )
        _assert_problem(
            await client.get("/api/v1/health", headers={"Origin": "http://127.0.0.1:9999"}),
            status=403,
            error_code="LOOPBACK_ORIGIN_FORBIDDEN",
        )
        _assert_problem(
            await client.get("/api/v1/health", headers={"Origin": "null"}),
            status=403,
            error_code="LOOPBACK_ORIGIN_FORBIDDEN",
        )
        _assert_problem(
            await client.get("/api/v1/health", headers={"Sec-Fetch-Site": "cross-site"}),
            status=403,
            error_code="LOOPBACK_ORIGIN_FORBIDDEN",
        )
        r_same = await client.get(
            "/api/v1/health",
            headers={
                "Origin": "http://127.0.0.1:8420",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert r_same.status_code == 200
        _assert_problem(
            await client.options("/api/v1/health", headers={"Origin": "http://evil.example"}),
            status=403,
            error_code="LOOPBACK_ORIGIN_FORBIDDEN",
        )

    # Raw ASGI peer IP check (remote peer IP rejected by default, accepted when opted-in)
    async def _raw_get(target_app: Any, *, client_ip: str, host: str) -> tuple[int, bytes]:
        sent: list[dict[str, Any]] = []
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/health",
            "raw_path": b"/api/v1/health",
            "query_string": b"",
            "headers": [(b"host", host.encode("ascii"))],
            "client": (client_ip, 55555),
            "server": ("10.0.0.5", 8420),
            "app": target_app,
        }

        async def _recv() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def _send(msg: dict[str, Any]) -> None:
            sent.append(msg)

        await target_app(scope, _recv, _send)
        start = next(m for m in sent if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return int(start["status"]), body

    st_blocked, body_blocked = await _raw_get(app, client_ip="203.0.113.10", host="127.0.0.1:8420")
    assert st_blocked == 403
    assert json.loads(body_blocked)["error_code"] == "LOOPBACK_ORIGIN_FORBIDDEN"

    remote_app = create_app(svc, allow_remote_unauthenticated=True, bound_host="10.0.0.5")
    st_ok, _ = await _raw_get(remote_app, client_ip="10.0.0.99", host="10.0.0.5:8420")
    assert st_ok == 200
    st_evil, _ = await _raw_get(remote_app, client_ip="10.0.0.99", host="evil.example:8420")
    assert st_evil == 403


@pytest.mark.asyncio
async def test_uat_v26_api_02_content_type_415_413_bodyless_and_dual_routes(
    tmp_path: Path,
) -> None:
    copy_public_am_wave1(tmp_path / "ws")

    def _sel(req: Any) -> MockTurnScript:
        if req.agent_id == "ship-vault":
            profs = load_project_profiles(svc, "p-media")
            return MockTurnScript(structured_output=build_wave1_am_draft("p-media", "0.1.0", profs))
        if req.agent_id == "dv-semantic-critic":
            return MockTurnScript(
                structured_output=SemanticCriticVerdict(
                    score=95,
                    accepted_waiver_ids=["WAIV-AP004", "WAIV-AP004-NAV"],
                )
            )
        if req.agent_id == "dv-horizon-critic":
            return MockTurnScript(
                structured_output=HorizonCriticVerdict(score=92, classification="REASONABLY_STABLE")
            )
        return MockTurnScript(text_output="{}")

    svc = _make_svc(tmp_path, harness=DeterministicMockHarness(selector=_sel))
    svc.init_project(ProjectInitReq(project_id="p-media", domain_namespace="asset_mgmt"))
    src = svc.attach_source(
        "p-media",
        SourceAttachReq(kind="sqlite", uri_or_filename="public_am/am_sec_treasury_ibor.sqlite"),
    )
    app = create_app(svc)

    # Commit minimal ontology & build vault so bodyless vault/verify returns 200
    bundle = svc.get_ontology_bundle("p-media")
    assert bundle.ontology.concepts == []
    proj = svc.get_project("p-media")
    assert proj.active_ontology_rev_id is not None
    md = svc.attach_source(
        "p-media",
        SourceAttachReq(kind="markdown", uri_or_filename="public_am/am_stewardship_domain_notes.md"),
    )
    pq = svc.attach_source(
        "p-media",
        SourceAttachReq(kind="parquet", uri_or_filename="public_am/am_esg_stewardship_proxy.parquet"),
    )
    md_quote = svc.get_document_pages("p-media", md.source_id, page_start=1, page_end=1)[0].masked_text.splitlines()[0].strip()
    ops = build_wave1_commit_ops(src.source_id, pq.source_id, md.source_id, md_quote)
    from opendv.models.wire_dtos_discovery import OntologyCommitReq
    from opendv.models.wire_dtos_vault import VaultBuildReq

    svc.commit_ontology(
        "p-media",
        OntologyCommitReq(
            operations=ops,  # type: ignore[arg-type]
            base_revision_id=proj.active_ontology_rev_id,
            rationale="Commit W1 ontology for media/415/413 UAT.",
        ),
    )
    res_b = await svc.build_vault("p-media", VaultBuildReq(policy="balanced"))
    assert res_b.status == "COMPLETED"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        for bad_ct in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
            r = await client.post(
                "/api/v1/projects",
                content=b"project_id=x",
                headers={"Content-Type": bad_ct},
            )
            _assert_problem(r, status=415, error_code="UNSUPPORTED_MEDIA_TYPE")

        # Bodyless POST without Content-Type passes
        r_ver = await client.post("/api/v1/projects/p-media/vault/verify")
        assert r_ver.status_code == 200
        assert r_ver.json()["passed"] is True

        # Dual PATCH /sources/{sid}: JSON preserves omitted options/selected_tables & multipart works
        r_patch_json = await client.patch(
            f"/api/v1/projects/p-media/sources/{src.source_id}",
            json={"uri_or_filename": "public_am/am_sec_treasury_ibor.sqlite"},
        )
        assert r_patch_json.status_code == 200

        csv_bytes = b"id,val\n1,a\n"
        csv_src = (
            await client.post(
                "/api/v1/projects/p-media/sources/upload",
                files={"file": ("sample.csv", csv_bytes, "text/csv")},
            )
        ).json()
        csv_sid = csv_src["source_id"]

        # Same-name multipart PATCH preserves table_name/table_id and serves updated rows
        r_patch_same = await client.patch(
            f"/api/v1/projects/p-media/sources/{csv_sid}",
            files={"file": ("sample.csv", b"id,val\n1,same_v2\n2,same_v3\n", "text/csv")},
        )
        assert r_patch_same.status_code == 200
        assert r_patch_same.json()["table_count"] == 1
        r_prev_same = await client.get(
            f"/api/v1/projects/p-media/evidence/tables/{csv_sid}:sample"
        )
        assert r_prev_same.status_code == 200
        assert [[str(x) for x in row] for row in r_prev_same.json()["rows"]] == [
            ["1", "same_v2"],
            ["2", "same_v3"],
        ]
        idx_same = (await client.get("/api/v1/projects/p-media/evidence/tables/index")).json()
        assert any(
            t["source_id"] == csv_sid and t["table_name"] == "sample" for t in idx_same
        )

        # Renamed multipart PATCH updates table_name/table_id and serves updated rows
        r_patch_mp = await client.patch(
            f"/api/v1/projects/p-media/sources/{csv_sid}",
            files={"file": ("sample_v2.csv", b"id,val\n1,b\n2,c\n", "text/csv")},
        )
        assert r_patch_mp.status_code == 200
        assert r_patch_mp.json()["table_count"] == 1
        r_prev_mp = await client.get(
            f"/api/v1/projects/p-media/evidence/tables/{csv_sid}:sample_v2"
        )
        assert r_prev_mp.status_code == 200
        assert [[str(x) for x in row] for row in r_prev_mp.json()["rows"]] == [
            ["1", "b"],
            ["2", "c"],
        ]

        # JSON PATCH on an uploaded source clears stale uploaded_relpath and reads new file
        json_rep_path = tmp_path / "ws" / "public_am" / "sample_json.csv"
        json_rep_path.write_text("id,val\n9,json_ok\n", encoding="utf-8")
        r_patch_up_json = await client.patch(
            f"/api/v1/projects/p-media/sources/{csv_sid}",
            json={"uri_or_filename": "public_am/sample_json.csv"},
        )
        assert r_patch_up_json.status_code == 200
        r_prev_json = await client.get(
            f"/api/v1/projects/p-media/evidence/tables/{csv_sid}:sample_json"
        )
        assert r_prev_json.status_code == 200
        assert [[str(x) for x in row] for row in r_prev_json.json()["rows"]] == [
            ["9", "json_ok"],
        ]
        assert (
            svc.state_manager.get_source_record("p-media", csv_sid)[
                "connector_config"
            ].uploaded_relpath
            is None
        )

        # Dual POST /projects/import: JSON path + multipart upload
        bundle_zip, _ = svc.export_bundle("p-media", mode="core_project_state")
        r_imp_json = await client.post(
            "/api/v1/projects/import",
            json={"bundle_path": str(bundle_zip), "conflict": "rename"},
        )
        assert r_imp_json.status_code == 200
        assert r_imp_json.json()["project_id"] == "p-media-copy"

        r_imp_mp = await client.post(
            "/api/v1/projects/import",
            files={"file": (bundle_zip.name, bundle_zip.read_bytes(), "application/zip")},
            data={"conflict": "rename"},
        )
        assert r_imp_mp.status_code == 200
        assert r_imp_mp.json()["resolved_conflict"] == "renamed"

        # 413 declared Content-Length > 250 MB
        for path in ("/api/v1/projects/p-media/sources/upload", "/api/v1/projects/import"):
            r_big = await client.post(
                path,
                content=b"x",
                headers={
                    "Content-Type": "multipart/form-data; boundary=x",
                    "Content-Length": "262144001",
                },
            )
            _assert_problem(r_big, status=413, error_code="ARCHIVE_SIZE_LIMIT_EXCEEDED")

    # 413 streaming chunked overflow via raw ASGI > 262,144,000 bytes
    sent_413: list[dict[str, Any]] = []
    chunks_sent = 0

    async def _big_recv() -> dict[str, Any]:
        nonlocal chunks_sent
        if chunks_sent == 0:
            chunks_sent += 1
            header = (
                b'--abc\r\nContent-Disposition: form-data; name="file"; filename="big.bin"\r\n'
                b"Content-Type: application/octet-stream\r\n\r\n"
            )
            return {"type": "http.request", "body": header + (b"\x00" * 1_000_000), "more_body": True}
        if chunks_sent < 263:
            chunks_sent += 1
            return {"type": "http.request", "body": b"\x00" * 1_000_000, "more_body": True}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _collect_send(msg: dict[str, Any]) -> None:
        sent_413.append(msg)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/projects/p-media/sources/upload",
            "raw_path": b"/api/v1/projects/p-media/sources/upload",
            "query_string": b"",
            "headers": [
                (b"host", b"127.0.0.1:8420"),
                (b"content-type", b"multipart/form-data; boundary=abc"),
                (b"transfer-encoding", b"chunked"),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8420),
            "app": app,
        },
        _big_recv,
        _collect_send,
    )
    start_413 = next(m for m in sent_413 if m["type"] == "http.response.start")
    body_413 = b"".join(m.get("body", b"") for m in sent_413 if m["type"] == "http.response.body")
    assert start_413["status"] == 413
    assert json.loads(body_413)["error_code"] == "ARCHIVE_SIZE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_uat_v26_api_03_problem_details_34_codes_validation_500_and_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert len(ERROR_MAPPINGS) == 34
    svc = _make_svc(tmp_path)
    svc.init_project(ProjectInitReq(project_id="p-err", domain_namespace="asset_mgmt"))
    app = create_app(svc)

    # Register temporary probe route inside test app to verify every ErrorCode & unhandled 500
    @app.get("/api/v1/_test/raise-code/{code}")
    def _raise_code(code: str) -> None:
        ec = ErrorCode(code)
        defect = (
            EmitterDefectReport(
                target_id="duckdb_local",
                stage="verify_sql_ast",
                rule_id="EVAL-AST",
                message="synthetic defect",
                sql_or_artifact_snippet="SELECT 1",
            )
            if ec == ErrorCode.EMITTER_VERIFICATION_FAILED
            else None
        )
        raise OpenDVError(f"Probe error {code}", error_code=ec, defect_report=defect)

    @app.get("/api/v1/_test/crash")
    def _crash() -> None:
        raise RuntimeError("boom_secret_detail")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        for code, entry in ERROR_MAPPINGS.items():
            if entry.http_status is None or entry.http_status < 400:
                continue
            r = await client.get(f"/api/v1/_test/raise-code/{code}")
            body = _assert_problem(r, status=entry.http_status, error_code=str(code))
            assert body["type"] == entry.problem_type_uri
            assert body["title"] == entry.problem_title
            if code == ErrorCode.EMITTER_VERIFICATION_FAILED:
                assert body["defect_report"]["rule_id"] == "EVAL-AST"

        # extra="forbid" on VaultBuildReq -> 400 SYNTAX_INVALID_ARGUMENT
        r_extra = await client.post(
            "/api/v1/projects/p-err/runs",
            json={"policy": "balanced", "dbt_sql_dialect": "duckdb"},
        )
        _assert_problem(r_extra, status=400, error_code="SYNTAX_INVALID_ARGUMENT")

    # Unhandled Exception -> 500 without traceback leak
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://127.0.0.1:8420",
    ) as crash_client:
        r_500 = await crash_client.get("/api/v1/_test/crash")
        b_500 = _assert_problem(r_500, status=500, error_code="INTERNAL_SERVER_ERROR")
        assert "boom_secret_detail" not in b_500["detail"]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:

        # OPENDV_WORKSPACE_ROOTS containment
        allowed = tmp_path / "allowed_root"
        outside = tmp_path / "outside_root"
        allowed.mkdir()
        outside.mkdir()
        (allowed / "ok.csv").write_text("id,v\n1,2\n", encoding="utf-8")
        (outside / "bad.csv").write_text("id,v\n1,2\n", encoding="utf-8")
        bundle_zip, _ = svc.export_bundle("p-err", mode="core_project_state")
        monkeypatch.setenv("OPENDV_WORKSPACE_ROOTS", str(allowed))

        _assert_problem(
            await client.post(
                "/api/v1/sources/inspect",
                json={"kind": "csv", "uri_or_path": str(outside / "bad.csv")},
            ),
            status=400,
            error_code="SYNTAX_INVALID_ARGUMENT",
        )
        _assert_problem(
            await client.post(
                "/api/v1/projects/p-err/sources",
                json={"kind": "csv", "uri_or_filename": str(outside / "bad.csv")},
            ),
            status=400,
            error_code="SYNTAX_INVALID_ARGUMENT",
        )
        _assert_problem(
            await client.post(
                "/api/v1/projects/import",
                json={"bundle_path": str(bundle_zip), "conflict": "rename"},
            ),
            status=400,
            error_code="SYNTAX_INVALID_ARGUMENT",
        )
        r_ok = await client.post(
            "/api/v1/sources/inspect",
            json={"kind": "csv", "uri_or_path": str(allowed / "ok.csv")},
        )
        assert r_ok.status_code == 200

    # Remote request without OPENDV_WORKSPACE_ROOTS -> 403 LOOPBACK_ORIGIN_FORBIDDEN
    monkeypatch.delenv("OPENDV_WORKSPACE_ROOTS", raising=False)
    remote_app = create_app(svc, allow_remote_unauthenticated=True, bound_host="10.0.0.5")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=remote_app),
        base_url="http://10.0.0.5:8420",
    ) as rclient:
        _assert_problem(
            await rclient.post(
                "/api/v1/sources/inspect",
                json={"kind": "csv", "uri_or_path": str(allowed / "ok.csv")},
            ),
            status=403,
            error_code="LOOPBACK_ORIGIN_FORBIDDEN",
        )


@pytest.mark.asyncio
async def test_uat_v26_api_04_conditional_spa_static_mount(tmp_path: Path) -> None:
    svc = _make_svc(tmp_path)
    gitkeep = Path(__file__).resolve().parents[2] / "src" / "opendv" / "api" / "static" / ".gitkeep"
    assert gitkeep.is_file() and gitkeep.stat().st_size == 0

    empty_static = tmp_path / "empty_static"
    empty_static.mkdir()
    unbuilt_app = create_app(svc, static_dir=empty_static)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=unbuilt_app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        assert (await client.get("/api/v1/health")).status_code == 200
        _assert_problem(await client.get("/"), status=404, error_code="RESOURCE_NOT_FOUND")

    default_app = create_app(svc)
    built_spa = (gitkeep.parent / "index.html").is_file()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=default_app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        assert (await client.get("/api/v1/health")).status_code == 200
        root_resp = await client.get("/")
        if built_spa:
            assert root_resp.status_code == 200
            assert "text/html" in root_resp.headers["content-type"]
        else:
            _assert_problem(root_resp, status=404, error_code="RESOURCE_NOT_FOUND")

    static_dir = tmp_path / "static_ui"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text("<html><body>OpenDV UI</body></html>", encoding="utf-8")
    (static_dir / "assets" / "app.js").write_text("console.log('ok');", encoding="utf-8")

    spa_app = create_app(svc, static_dir=static_dir)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=spa_app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        for spa_route in ("/", "/projects/p1/step/1", "/explorer", "/runs", "/settings"):
            r = await client.get(spa_route)
            assert r.status_code == 200
            assert "text/html" in r.headers["content-type"]
            assert "OpenDV UI" in r.text
        r_js = await client.get("/assets/app.js")
        assert r_js.status_code == 200
        assert "application/javascript" in r_js.headers["content-type"]
        assert (await client.get("/api/v1/health")).status_code == 200
        _assert_problem(
            await client.get("/api/v1/nonexistent"),
            status=404,
            error_code="RESOURCE_NOT_FOUND",
        )


@pytest.mark.asyncio
async def test_uat_v26_api_05_step4_step6_202_runs_logs_pause_and_sse_replay(
    tmp_path: Path,
) -> None:
    copy_public_am_wave1(tmp_path / "ws")
    pause_started = asyncio.Event()
    pause_release = asyncio.Event()

    class _BarrierHarness(DeterministicMockHarness):
        async def run_turn(
            self,
            request: Any,
            *,
            event_sink: Any = None,
            scout_dispatcher: Any = None,
        ) -> Any:
            if request.metadata.get("discovery_mode") == "full_rebuild" and request.checkpoint is None:
                pause_started.set()
                await pause_release.wait()
                return await super().run_turn(
                    request, event_sink=event_sink, scout_dispatcher=scout_dispatcher
                )
            if request.agent_id == "ship-vault" and request.cegar_policy == "fast":
                await asyncio.sleep(0.02)
                raise RuntimeError("synthetic_async_boom")
            return await super().run_turn(
                request, event_sink=event_sink, scout_dispatcher=scout_dispatcher
            )

    def _sel(req: Any) -> MockTurnScript:
        if req.agent_id == "ontology-first-mate":
            if req.metadata.get("discovery_mode") == "full_rebuild":
                return MockTurnScript(
                    structured_output={
                        "proposed_ops": [],
                        "clarification_questions": [],
                        "summary": "Pause/resume turn",
                    }
                )
            if req.checkpoint is None:
                return MockTurnScript(
                    structured_output={
                        "proposed_ops": [],
                        "clarification_questions": [
                            ClarificationQuestion(
                                question_id="q-s4-hold",
                                concept_name="Filer",
                                title="Confirm CIK",
                                prompt="Use SEC CIK?",
                                sample_rows=["0001"],
                                impact_if_locked="Locks BK.",
                                recommended_answer="Yes",
                            ).model_dump(mode="json")
                        ],
                        "summary": "Hold",
                    }
                )
            return MockTurnScript(
                structured_output={
                    "proposed_ops": [
                        AddConcept(
                            concept_uri="urn:asset_mgmt:Filer",
                            concept_name="Filer",
                            description="Filer concept.",
                            key_authority_tier="TIER_1_GLOBAL_STANDARD",
                            attributes=[
                                ConceptAttribute(
                                    name="filer_cik",
                                    logical_type="UTF8_STRING",
                                    sql_type_hint="VARCHAR",
                                    nullable=False,
                                    is_business_key=True,
                                    is_pii=False,
                                )
                            ],
                            table_bindings=[
                                ConceptTableBinding(
                                    source_id=src.source_id,
                                    table_id=f"{src.source_id}:institutional_filers",
                                    col_name="filer_cik",
                                )
                            ],
                        ).model_dump(mode="json")
                    ],
                    "clarification_questions": [],
                    "summary": "Done",
                }
            )
        if req.agent_id == "ship-vault":
            profs = load_project_profiles(svc, "p-sse")
            return MockTurnScript(structured_output=build_wave1_am_draft("p-sse", "0.1.0", profs))
        if req.agent_id == "dv-semantic-critic":
            return MockTurnScript(
                structured_output=SemanticCriticVerdict(
                    score=95,
                    accepted_waiver_ids=["WAIV-AP004", "WAIV-AP004-NAV"],
                )
            )
        if req.agent_id == "dv-horizon-critic":
            return MockTurnScript(
                structured_output=HorizonCriticVerdict(score=93, classification="REASONABLY_STABLE")
            )
        return MockTurnScript(text_output="{}")

    svc = _make_svc(tmp_path, harness=_BarrierHarness(selector=_sel))
    svc.init_project(ProjectInitReq(project_id="p-sse", domain_namespace="asset_mgmt"))
    src = svc.attach_source(
        "p-sse",
        SourceAttachReq(kind="sqlite", uri_or_filename="public_am/am_sec_treasury_ibor.sqlite"),
    )
    pq = svc.attach_source(
        "p-sse",
        SourceAttachReq(kind="parquet", uri_or_filename="public_am/am_esg_stewardship_proxy.parquet"),
    )
    md = svc.attach_source(
        "p-sse",
        SourceAttachReq(kind="markdown", uri_or_filename="public_am/am_stewardship_domain_notes.md"),
    )
    app = create_app(svc)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        # 1) Step 4 Hold -> SSE -> Answers -> Completed
        r_s4 = await client.post("/api/v1/projects/p-sse/ontology/construct", json={})
        assert r_s4.status_code == 202
        rid4 = r_s4.json()["run_id"]
        frames4 = await wait_run_via_sse(client, f"/api/v1/projects/p-sse/ontology/runs/{rid4}/events")
        assert any(f.get("event") == "HOLD_CHECKPOINTED" for f in frames4)

        _assert_problem(
            await client.post(
                f"/api/v1/projects/p-sse/ontology/runs/{rid4}/controls",
                json={"action": "accept-best"},
            ),
            status=400,
            error_code="SYNTAX_INVALID_ARGUMENT",
        )
        r_ans4 = await client.post(
            f"/api/v1/projects/p-sse/ontology/runs/{rid4}/answers",
            json={"answers": {"q-s4-hold": "Yes"}},
        )
        assert r_ans4.status_code == 202
        frames4_done = await wait_run_via_sse(
            client, f"/api/v1/projects/p-sse/ontology/runs/{rid4}/events"
        )
        assert any(f.get("event") == "RUN_COMPLETED" for f in frames4_done)
        assert (
            await client.get(f"/api/v1/projects/p-sse/ontology/runs/{rid4}")
        ).json()["status"] == "COMPLETED"

        # 2) Step 4 pause while RUNNING -> HOLD_CHECKPOINTED(pending_questions=[]) -> answers({}) -> COMPLETED
        r_pause_start = await client.post(
            "/api/v1/projects/p-sse/ontology/construct",
            json={"discovery_mode": "full_rebuild"},
        )
        assert r_pause_start.status_code == 202
        rid_p = r_pause_start.json()["run_id"]
        await asyncio.wait_for(pause_started.wait(), timeout=5.0)
        r_ctrl_p = await client.post(
            f"/api/v1/projects/p-sse/ontology/runs/{rid_p}/controls",
            json={"action": "pause"},
        )
        assert r_ctrl_p.status_code == 200
        assert r_ctrl_p.json()["status"] == "HOLD_REQUESTED"
        pause_release.set()
        frames_p = await wait_run_via_sse(
            client, f"/api/v1/projects/p-sse/ontology/runs/{rid_p}/events"
        )
        assert any(f.get("event") == "HOLD_CHECKPOINTED" for f in frames_p)
        st_p = (await client.get(f"/api/v1/projects/p-sse/ontology/runs/{rid_p}")).json()
        assert st_p["status"] == "HOLD_CHECKPOINTED" and st_p["pending_questions"] == []
        r_res_p = await client.post(
            f"/api/v1/projects/p-sse/ontology/runs/{rid_p}/answers",
            json={"answers": {}},
        )
        assert r_res_p.status_code == 202
        await wait_run_via_sse(client, f"/api/v1/projects/p-sse/ontology/runs/{rid_p}/events")
        assert (
            await client.get(f"/api/v1/projects/p-sse/ontology/runs/{rid_p}")
        ).json()["status"] == "COMPLETED"

        # 3) Commit W1 ontology, test Step 6 crash boundary + RUN_FAILED + lease release + Step 6 success + SSE replay
        proj = svc.get_project("p-sse")
        assert proj.active_ontology_rev_id is not None
        md_q = svc.get_document_pages("p-sse", md.source_id, page_start=1, page_end=1)[0].masked_text.splitlines()[0].strip()
        await client.post(
            "/api/v1/projects/p-sse/ontology/commit",
            json={
                "operations": build_wave1_commit_ops(src.source_id, pq.source_id, md.source_id, md_q),
                "base_revision_id": proj.active_ontology_rev_id,
                "rationale": "Commit W1 ontology for Step 6 SSE tests.",
            },
        )

        # Crash boundary: fast policy triggers RuntimeError asynchronously
        r_crash = await client.post("/api/v1/projects/p-sse/runs", json={"policy": "fast"})
        assert r_crash.status_code == 202
        rid_crash = r_crash.json()["run_id"]
        crash_frames = await wait_run_via_sse(client, f"/api/v1/projects/p-sse/runs/{rid_crash}/events")
        assert any(f.get("event") == "RUN_FAILED" for f in crash_frames)
        assert (await client.get(f"/api/v1/projects/p-sse/runs/{rid_crash}")).json()["status"] == "FAILED"

        # Subsequent run succeeds because lease was released cleanly
        r_s6 = await client.post("/api/v1/projects/p-sse/runs", json={"policy": "balanced"})
        assert r_s6.status_code == 202
        rid6 = r_s6.json()["run_id"]
        frames6 = await wait_run_via_sse(client, f"/api/v1/projects/p-sse/runs/{rid6}/events")
        assert any(f.get("event") == "RUN_COMPLETED" for f in frames6)

        # ?step= filter & newest-first
        runs_s4 = (await client.get("/api/v1/projects/p-sse/runs?step=step4")).json()
        assert len(runs_s4) == 2 and runs_s4[0]["run_id"] == rid_p
        runs_s6 = (await client.get("/api/v1/projects/p-sse/runs?step=step6")).json()
        assert len(runs_s6) == 2 and runs_s6[0]["run_id"] == rid6

        # Logs pagination & SSE Last-Event-ID + incomplete trailing line guard
        logs_page = (await client.get(f"/api/v1/projects/p-sse/runs/{rid6}/logs?since_seq=1&limit=2")).json()
        assert len(logs_page["events"]) <= 2
        log_path = svc.state_manager.run_log_path("p-sse", rid6)
        with log_path.open("ab") as fh:
            fh.write(b'{"seq": 9999, "incomplete')  # no trailing newline

        _assert_problem(
            await client.get(
                f"/api/v1/projects/p-sse/runs/{rid6}/events",
                headers={"Last-Event-ID": "not-an-int"},
            ),
            status=400,
            error_code="SYNTAX_INVALID_ARGUMENT",
        )
        replay = await wait_run_via_sse(
            client,
            f"/api/v1/projects/p-sse/runs/{rid6}/events",
            last_event_id="1",
        )
        assert all(int(f["id"]) > 1 for f in replay)
        assert all(int(f["id"]) != 9999 for f in replay)

        # Offline METADATA_ONLY_BINARY_ABSENT cached sample_values preview
        bundle_zip, _ = svc.export_bundle("p-sse", mode="core_project_state")
        imp = (
            await client.post(
                "/api/v1/projects/import",
                json={"bundle_path": str(bundle_zip), "conflict": "rename"},
            )
        ).json()
        copy_pid = imp["project_id"]
        copy_tbls = (await client.get(f"/api/v1/projects/{copy_pid}/evidence/tables/index")).json()
        copy_tid = copy_tbls[0]["table_id"]
        _assert_problem(
            await client.get(f"/api/v1/projects/{copy_pid}/evidence/tables/{copy_tid}?offset=-1"),
            status=400,
            error_code="SYNTAX_INVALID_ARGUMENT",
        )
        r_prev = await client.get(f"/api/v1/projects/{copy_pid}/evidence/tables/{copy_tid}")
        assert r_prev.status_code == 200
        assert len(r_prev.json()["rows"]) > 0


@pytest.mark.asyncio
async def test_uat_v26_api_06_all_70_routes_scale_200_and_public_am_w001_w002(
    tmp_path: Path,
) -> None:
    # Verify OpenAPI schema exposes exactly 72 operations and expected schemas
    schema = export_openapi_schema()
    op_count = sum(len(methods) for methods in schema["paths"].values())
    assert op_count == 72
    for comp in (
        "ProjectImportPathReq",
        "SourceReattachReq",
        "ProblemDetails",
        "ErrorRemediation",
        "ReadinessResponse",
        "ProviderReadinessRecord",
        "DoctorResponse",
        "ProjectSettingsPatchReq",
        "RevokeTrustedWorkspaceReq",
        "OntologyGroundReq",
        "GroundingResponse",
    ):
        assert comp in schema["components"]["schemas"]

    ws = tmp_path / "ws"
    copy_public_am_wave1(ws)
    scale_db = build_scale_200_sqlite(ws / "scale_200.sqlite")

    wave_mode: dict[str, Any] = {"wave": 1}
    fake_mcp = LoopbackFakeExternalMcpServer(server_id="fake-fibo-mcp")
    fake_mcp.start()

    class _McpMockHarness(DeterministicMockHarness):
        async def run_turn(
            self,
            request: Any,
            *,
            event_sink: Any = None,
            scout_dispatcher: Any = None,
        ) -> Any:
            if request.agent_id == "ontology-first-mate" and request.checkpoint is None:
                gw = ExternalMcpGateway(
                    state_store=svc.state_manager,
                    secret_store=svc.secret_store,
                    settings_store=svc.settings_store,
                )
                await gw.invoke_tool(
                    project_id="am-e2e",
                    run_id=request.run_id,
                    server_id="fake-fibo-mcp",
                    tool_name="lookup_entity",
                    arguments={"symbol": "Filer"},
                )
            return await super().run_turn(
                request, event_sink=event_sink, scout_dispatcher=scout_dispatcher
            )

    def _sel(req: Any) -> MockTurnScript:
        if req.agent_id == "ontology-first-mate":
            if req.metadata.get("discovery_mode") == "full_rebuild":
                return MockTurnScript(
                    structured_output={
                        "proposed_ops": [],
                        "clarification_questions": [],
                        "summary": "Pause/resume turn",
                    }
                )
            if req.checkpoint is None:
                return MockTurnScript(
                    structured_output={
                        "proposed_ops": [],
                        "clarification_questions": [
                            ClarificationQuestion(
                                question_id="q-w1-bk",
                                concept_name="Institutional Filer",
                                title="Confirm SEC CIK",
                                prompt="Confirm SEC CIK.",
                                sample_rows=["0001166559"],
                                impact_if_locked="Locks hub_filer.",
                                recommended_answer="Confirm SEC_CIK",
                            ).model_dump(mode="json")
                        ],
                        "summary": "Hold for SEC CIK",
                    },
                )
            srcs = svc.list_sources("am-e2e")
            sql_s = next(s for s in srcs if s.kind == "sqlite")
            return MockTurnScript(
                structured_output={
                    "proposed_ops": [
                        AddConcept(
                            concept_uri="urn:asset_mgmt:StagedCustody",
                            concept_name="Staged Custody",
                            description="Uncommitted staged custody concept.",
                            key_authority_tier="TIER_2_ENTERPRISE_SOR",
                            table_bindings=[
                                ConceptTableBinding(
                                    source_id=sql_s.source_id,
                                    table_id=f"{sql_s.source_id}:institutional_filers",
                                    col_name="filer_cik",
                                )
                            ],
                        ).model_dump(mode="json")
                    ],
                    "clarification_questions": [],
                    "summary": "Staged custody candidate",
                }
            )
        if req.agent_id == "ship-vault":
            profs = load_project_profiles(svc, "am-e2e")
            if wave_mode["wave"] == 1:
                return MockTurnScript(
                    structured_output=build_wave1_am_draft("am-e2e", "0.1.0", profs)
                )
            if not wave_mode.get("w2_held"):
                wave_mode["w2_held"] = True
                return MockTurnScript(
                    structured_output=build_wave1_am_draft("am-e2e", "0.2.0", profs).model_copy(
                        update={
                            "wave_number": 2,
                            "pending_clarifications": [
                                ClarificationQuestion(
                                    question_id="q-otc-nk",
                                    concept_name="OtcCollateral",
                                    title="Select OTC natural key",
                                    prompt="Choose uti_id or isda_master_id",
                                    sample_rows=["uti_id=UTI-1, isda_master_id=ISDA-1"],
                                    impact_if_locked="Locks OTC Hub BK.",
                                    recommended_answer="uti_id",
                                )
                            ],
                        }
                    )
                )
            return MockTurnScript(structured_output=build_wave2_am_draft("am-e2e", "0.2.0", profs))
        if req.agent_id == "dv-semantic-critic":
            return MockTurnScript(
                structured_output=SemanticCriticVerdict(
                    score=95,
                    accepted_waiver_ids=["WAIV-AP004", "WAIV-AP004-NAV"],
                )
            )
        if req.agent_id == "dv-horizon-critic":
            if wave_mode["wave"] == 2 and not wave_mode.get("horizon_done"):
                wave_mode["horizon_done"] = True
                return MockTurnScript(
                    structured_output=HorizonCriticVerdict(
                        score=72,
                        classification="HUMAN_REVIEW_REQUIRED",
                        bucket_c_watchlist=["Verify sal_issuer mapping"],
                    )
                )
            return MockTurnScript(
                structured_output=HorizonCriticVerdict(score=93, classification="REASONABLY_STABLE")
            )
        return MockTurnScript(text_output="{}")

    state_dir = tmp_path / "state"
    settings_store = SettingsStore(user_state_dir=state_dir)
    secret_store = KeyringSecretStore(ws, state_dir=state_dir, settings_store=settings_store)
    state_mgr = ProjectStateManager(ws)
    state_mgr.initialize()

    async def _hermetic_probe(
        _provider: Any, _bin: str | None, _key: str | None, _env: Any
    ) -> Tier2ProbeOutcome:
        return Tier2ProbeOutcome(kind="verified")

    readiness_svc = HarnessReadinessService(
        settings_store=settings_store,
        secret_store=secret_store,
        db_path=state_mgr.db_path,
        which_fn=lambda _: None,
        find_spec_fn=lambda _: None,
        probe_fn=_hermetic_probe,
    )
    svc = OpenDVService(
        ws,
        state_dir=state_dir,
        harness=_McpMockHarness(selector=_sel),
        readiness_service=readiness_svc,
    )
    app = create_app(svc)

    # Collect all 70 route signatures (method, path) recursively from FastAPI router
    expected_ops: set[tuple[str, str]] = set()

    def _walk_routes(routes: list[Any], prefix: str = "") -> None:
        for route in routes:
            if isinstance(route, APIRoute) and route.include_in_schema:
                full_path = f"{prefix}{route.path}"
                if full_path.startswith("/api/v1"):
                    for m in route.methods:
                        if m not in {"HEAD", "OPTIONS"}:
                            expected_ops.add((m, full_path))
            elif hasattr(route, "original_router"):
                inc_ctx = getattr(route, "include_context", None)
                sub_prefix = getattr(inc_ctx, "prefix", "") or ""
                _walk_routes(list(route.original_router.routes), f"{prefix}{sub_prefix}")
            elif hasattr(route, "routes"):
                sub_prefix = getattr(route, "prefix", "") or ""
                _walk_routes(list(route.routes), f"{prefix}{sub_prefix}")

    _walk_routes(list(app.routes))
    assert len(expected_ops) == 72
    called_ops: set[tuple[str, str]] = set()

    def _mark(method: str, path_tmpl: str) -> None:
        assert (method, path_tmpl) in expected_ops, f"Unexpected route {(method, path_tmpl)}"
        called_ops.add((method, path_tmpl))

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8420",
        ) as client:
            # --- Group 1: System, Settings, Secrets, MCP, Agents (11 routes) ---
            assert (await client.get("/api/v1/health")).status_code == 200
            _mark("GET", "/api/v1/health")

            cur_settings = (await client.get("/api/v1/settings")).json()
            _mark("GET", "/api/v1/settings")
            assert (await client.put("/api/v1/settings", json=cur_settings)).status_code == 200
            _mark("PUT", "/api/v1/settings")

            secret_val = "sk-super-secret-value-9999"
            assert (
                await client.post("/api/v1/secrets", json={"key": "TEST_API_KEY", "value": secret_val})
            ).status_code == 200
            _mark("POST", "/api/v1/secrets")
            secs = (await client.get("/api/v1/secrets?keys=TEST_API_KEY")).json()
            _mark("GET", "/api/v1/secrets")
            assert secs[0]["present"] is True
            assert secret_val not in json.dumps(secs)
            assert (await client.delete("/api/v1/secrets/TEST_API_KEY")).status_code == 204
            _mark("DELETE", "/api/v1/secrets/{key}")

            mcp_spec = fake_mcp.server_spec().model_dump(mode="json")
            assert (await client.post("/api/v1/mcp/servers", json=mcp_spec)).status_code == 200
            _mark("POST", "/api/v1/mcp/servers")
            assert len((await client.get("/api/v1/mcp/servers")).json()) == 1
            _mark("GET", "/api/v1/mcp/servers")

            agents = (await client.get("/api/v1/agents")).json()
            _mark("GET", "/api/v1/agents")
            assert len(agents) == 6
            assert (await client.get(f"/api/v1/agents/{agents[0]['agent_id']}")).status_code == 200
            _mark("GET", "/api/v1/agents/{agent_id}")

            r_ready = await client.get("/api/v1/agents/readiness")
            assert r_ready.status_code == 200
            assert len(r_ready.json()["providers"]) == 4
            _mark("GET", "/api/v1/agents/readiness")

            r_probe = await client.post("/api/v1/agents/readiness/test", json={"provider": "agy"})
            assert r_probe.status_code == 200
            assert r_probe.json()["status"] == "MISSING_PREREQ"
            _mark("POST", "/api/v1/agents/readiness/test")

            r_doc = await client.get("/api/v1/system/doctor")
            assert r_doc.status_code == 200
            _mark("GET", "/api/v1/system/doctor")

            svc.trust_workspace_path(ws)
            r_rev = await client.post(
                "/api/v1/settings/trusted-workspaces/revoke",
                json={"workspace_root": str(ws)},
            )
            assert r_rev.status_code == 200
            assert r_rev.json()["trusted_workspaces"] == []
            _mark("POST", "/api/v1/settings/trusted-workspaces/revoke")

            # --- Group 2: Seeds, Starter Packs, Projects, Bundle (10 routes) ---
            assert (await client.get("/api/v1/workspace-seeds")).status_code == 200
            _mark("GET", "/api/v1/workspace-seeds")
            packs = (await client.get("/api/v1/starter-packs")).json()
            _mark("GET", "/api/v1/starter-packs")
            assert (await client.get(f"/api/v1/starter-packs/{packs[0]['pack_id']}/preview")).status_code == 200
            _mark("GET", "/api/v1/starter-packs/{pack_id}/preview")

            assert (
                await client.post(
                    "/api/v1/projects",
                    json={"project_id": "am-e2e", "domain_namespace": "asset_mgmt"},
                )
            ).status_code == 200
            _mark("POST", "/api/v1/projects")
            assert any(p["project_id"] == "am-e2e" for p in (await client.get("/api/v1/projects")).json())
            _mark("GET", "/api/v1/projects")
            proj = (await client.get("/api/v1/projects/am-e2e")).json()
            _mark("GET", "/api/v1/projects/{project_id}")
            assert (
                await client.patch(
                    "/api/v1/projects/am-e2e/settings",
                    json={"proving_sample_rows": 500},
                )
            ).status_code == 200
            _mark("PATCH", "/api/v1/projects/{project_id}/settings")
            proj_settings = (await client.get("/api/v1/projects/am-e2e")).json()["settings_snapshot"]
            assert (
                await client.put("/api/v1/projects/am-e2e/settings", json=proj_settings)
            ).status_code == 200
            _mark("PUT", "/api/v1/projects/{project_id}/settings")

            r_ps = await client.patch(
                "/api/v1/projects/am-e2e/settings",
                json={"discovery_mode": "incremental_new"},
            )
            assert r_ps.status_code == 200
            assert r_ps.json()["settings_snapshot"]["discovery_mode"] == "incremental_new"
            _mark("PATCH", "/api/v1/projects/{project_id}/settings")

            # --- Group 3: Sources & Evidence Navigator (14 routes) ---
            assert len((await client.get("/api/v1/sources/adapters")).json()) >= 5
            _mark("GET", "/api/v1/sources/adapters")
            assert (
                await client.post(
                    "/api/v1/sources/inspect",
                    json={"kind": "sqlite", "uri_or_path": "public_am/am_sec_treasury_ibor.sqlite"},
                )
            ).status_code == 200
            _mark("POST", "/api/v1/sources/inspect")

            # Attach Wave 1 sources
            sql_src = (
                await client.post(
                    "/api/v1/projects/am-e2e/sources",
                    json={"kind": "sqlite", "uri_or_filename": "public_am/am_sec_treasury_ibor.sqlite"},
                )
            ).json()
            _mark("POST", "/api/v1/projects/{project_id}/sources")
            pq_src = (
                await client.post(
                    "/api/v1/projects/am-e2e/sources",
                    json={"kind": "parquet", "uri_or_filename": "public_am/am_esg_stewardship_proxy.parquet"},
                )
            ).json()
            md_src = (
                await client.post(
                    "/api/v1/projects/am-e2e/sources",
                    json={"kind": "markdown", "uri_or_filename": "public_am/am_stewardship_domain_notes.md"},
                )
            ).json()
            pdf_src = (
                await client.post(
                    "/api/v1/projects/am-e2e/sources",
                    json={"kind": "pdf", "uri_or_filename": "public_am/am_custody_and_voting_guide.pdf"},
                )
            ).json()

            # Upload + Patch + Delete scratch source to cover those routes
            up_src = (
                await client.post(
                    "/api/v1/projects/am-e2e/sources/upload",
                    files={"file": ("scratch.csv", b"a,b\n1,2\n", "text/csv")},
                )
            ).json()
            _mark("POST", "/api/v1/projects/{project_id}/sources/upload")
            assert (
                await client.patch(
                    f"/api/v1/projects/am-e2e/sources/{up_src['source_id']}",
                    files={"file": ("scratch.csv", b"a,b\n1,3\n", "text/csv")},
                )
            ).status_code == 200
            _mark("PATCH", "/api/v1/projects/{project_id}/sources/{sid}")
            assert (
                await client.delete(f"/api/v1/projects/am-e2e/sources/{up_src['source_id']}")
            ).status_code == 204
            _mark("DELETE", "/api/v1/projects/{project_id}/sources/{sid}")
            assert len((await client.get("/api/v1/projects/am-e2e/sources")).json()) == 4
            _mark("GET", "/api/v1/projects/{project_id}/sources")

            # Evidence: tables index, table preview (no source_id), doc maps, scan block -> approve-scan -> pages, concept memory
            t_idx = (await client.get("/api/v1/projects/am-e2e/evidence/tables/index")).json()
            _mark("GET", "/api/v1/projects/{project_id}/evidence/tables/index")
            assert len(t_idx) == 7
            prev_t = (
                await client.get(
                    f"/api/v1/projects/am-e2e/evidence/tables/{sql_src['source_id']}:institutional_filers"
                )
            ).json()
            _mark("GET", "/api/v1/projects/{project_id}/evidence/tables/{tid}")
            assert prev_t["total_rows"] > 0

            doc_maps = (await client.get("/api/v1/projects/am-e2e/evidence/documents/map")).json()
            _mark("GET", "/api/v1/projects/{project_id}/evidence/documents/map")
            assert len(doc_maps) == 2

            _assert_problem(
                await client.get(
                    f"/api/v1/projects/am-e2e/evidence/documents/{pdf_src['source_id']}/pages?page_start=1&page_end=6"
                ),
                status=422,
                error_code="UNREDACTED_SCAN_BLOCKED",
            )
            assert (
                await client.post(
                    f"/api/v1/projects/am-e2e/evidence/documents/{pdf_src['source_id']}/approve-scan",
                    json={"page_numbers": [4], "rationale": "Approve page 4 table scan."},
                )
            ).status_code == 200
            _mark("POST", "/api/v1/projects/{project_id}/evidence/documents/{did}/approve-scan")
            pdf_pages = (
                await client.get(
                    f"/api/v1/projects/am-e2e/evidence/documents/{pdf_src['source_id']}/pages?page_start=1&page_end=6"
                )
            ).json()
            _mark("GET", "/api/v1/projects/{project_id}/evidence/documents/{did}/pages")
            assert len(pdf_pages) == 6

            assert (
                await client.post(
                    "/api/v1/projects/am-e2e/evidence/concept-memory",
                    json={"term": "Institutional Filer", "state": "PINNED"},
                )
            ).status_code == 200
            _mark("POST", "/api/v1/projects/{project_id}/evidence/concept-memory")
            assert len((await client.get("/api/v1/projects/am-e2e/evidence/concept-memory")).json()) == 1
            _mark("GET", "/api/v1/projects/{project_id}/evidence/concept-memory")

            # --- Group 4: Steps 4-5 Discovery & Ontology (12 routes) ---
            r_c4 = await client.post("/api/v1/projects/am-e2e/ontology/construct", json={})
            _mark("POST", "/api/v1/projects/{project_id}/ontology/construct")
            assert r_c4.status_code == 202
            rid4 = r_c4.json()["run_id"]
            await wait_run_via_sse(client, f"/api/v1/projects/am-e2e/ontology/runs/{rid4}/events")
            _mark("GET", "/api/v1/projects/{project_id}/ontology/runs/{rid}/events")

            assert (
                await client.post(
                    f"/api/v1/projects/am-e2e/ontology/runs/{rid4}/controls",
                    json={"action": "hint", "hint": "Keep SEC_CIK"},
                )
            ).status_code == 200
            _mark("POST", "/api/v1/projects/{project_id}/ontology/runs/{rid}/controls")

            assert (
                await client.post(
                    f"/api/v1/projects/am-e2e/ontology/runs/{rid4}/answers",
                    json={"answers": {"q-w1-bk": "Confirm SEC_CIK"}},
                )
            ).status_code == 202
            _mark("POST", "/api/v1/projects/{project_id}/ontology/runs/{rid}/answers")
            await wait_run_via_sse(client, f"/api/v1/projects/am-e2e/ontology/runs/{rid4}/events")
            assert (
                await client.get(f"/api/v1/projects/am-e2e/ontology/runs/{rid4}")
            ).json()["status"] == "COMPLETED"
            _mark("GET", "/api/v1/projects/{project_id}/ontology/runs/{rid}")

            # External MCP receipt persisted during Step 4 scout call
            receipts = (await client.get("/api/v1/projects/am-e2e/receipts")).json()
            _mark("GET", "/api/v1/projects/{project_id}/receipts")
            assert len(receipts) >= 1 and receipts[0]["server_id"] == "fake-fibo-mcp"

            ont_bundle = (await client.get("/api/v1/projects/am-e2e/ontology")).json()
            _mark("GET", "/api/v1/projects/{project_id}/ontology")
            assert len(ont_bundle["staged_candidates"]) >= 1

            ttl_staged = (
                await client.get("/api/v1/projects/am-e2e/ontology/export?format=ttl&include_staged=true")
            ).text
            _mark("GET", "/api/v1/projects/{project_id}/ontology/export")
            assert "Staged Custody" in ttl_staged

            assert (
                await client.post("/api/v1/ontology/fibo-qa", json={"query": "Issuer", "top_k": 3})
            ).status_code == 200
            _mark("POST", "/api/v1/ontology/fibo-qa")
            assert (
                await client.post(
                    "/api/v1/projects/am-e2e/ontology/fibo-qa",
                    json={"query": "Institutional Investor", "top_k": 3},
                )
            ).status_code == 200
            _mark("POST", "/api/v1/projects/{project_id}/ontology/fibo-qa")

            gnd_resp = await client.post(
                "/api/v1/ontology/ground",
                json={
                    "project_id": "am-e2e",
                    "policy": {"mode": "deterministic_only", "skip_reason": "route_smoke"},
                },
            )
            assert gnd_resp.status_code == 200
            _mark("POST", "/api/v1/ontology/ground")

            md_pages = (
                await client.get(
                    f"/api/v1/projects/am-e2e/evidence/documents/{md_src['source_id']}/pages?page_start=1&page_end=1"
                )
            ).json()
            md_quote = md_pages[0]["masked_text"].splitlines()[0].strip()
            cit = (
                await client.post(
                    "/api/v1/projects/am-e2e/ontology/citations/resolve",
                    json={"doc_id": md_src["source_id"], "page_number": 1, "verbatim_quote": md_quote},
                )
            ).json()
            _mark("POST", "/api/v1/projects/{project_id}/ontology/citations/resolve")
            assert cit["matched"] is True

            w1_ops = build_wave1_commit_ops(sql_src["source_id"], pq_src["source_id"], md_src["source_id"], md_quote)
            assert (
                await client.post(
                    "/api/v1/projects/am-e2e/ontology/validate",
                    json={"operations": w1_ops},
                )
            ).json()["valid"] is True
            _mark("POST", "/api/v1/projects/{project_id}/ontology/validate")

            rev1 = (
                await client.post(
                    "/api/v1/projects/am-e2e/ontology/commit",
                    json={
                        "operations": w1_ops,
                        "base_revision_id": proj["active_ontology_rev_id"],
                        "rationale": "Steward commits Wave 1 AM ontology.",
                    },
                )
            ).json()
            _mark("POST", "/api/v1/projects/{project_id}/ontology/commit")

            # --- Groups 5-6: Wave 1 Step 6 & Step 7 ---
            r_b1 = await client.post("/api/v1/projects/am-e2e/runs", json={"policy": "balanced"})
            _mark("POST", "/api/v1/projects/{project_id}/runs")
            rid_w1 = r_b1.json()["run_id"]
            await wait_run_via_sse(client, f"/api/v1/projects/am-e2e/runs/{rid_w1}/events")
            _mark("GET", "/api/v1/projects/{project_id}/runs/{rid}/events")

            assert len((await client.get("/api/v1/projects/am-e2e/runs")).json()) >= 2
            _mark("GET", "/api/v1/projects/{project_id}/runs")
            assert (await client.get(f"/api/v1/projects/am-e2e/runs/{rid_w1}")).json()["status"] == "COMPLETED"
            _mark("GET", "/api/v1/projects/{project_id}/runs/{rid}")
            assert (await client.get(f"/api/v1/projects/am-e2e/runs/{rid_w1}/logs")).status_code == 200
            _mark("GET", "/api/v1/projects/{project_id}/runs/{rid}/logs")
            assert len((await client.get(f"/api/v1/projects/am-e2e/runs/{rid_w1}/attempts")).json()) >= 1
            _mark("GET", "/api/v1/projects/{project_id}/runs/{rid}/attempts")
            assert (await client.get(f"/api/v1/projects/am-e2e/runs/{rid_w1}/guided-view")).status_code == 200
            _mark("GET", "/api/v1/projects/{project_id}/runs/{rid}/guided-view")
            assert (await client.get("/api/v1/projects/am-e2e/vault/ir")).status_code == 200
            _mark("GET", "/api/v1/projects/{project_id}/vault/ir")

            waiv = (
                await client.post(
                    "/api/v1/projects/am-e2e/vault/waivers",
                    json={
                        "waiver_id": "WAIV-REQ",
                        "rule_id": "DV-AP-004",
                        "entity_name": "hub_filer",
                        "rationale": "Filer CIK approved by Steward governance board.",
                    },
                )
            ).json()
            _mark("POST", "/api/v1/projects/{project_id}/vault/waivers")
            assert waiv["waiver_id"].startswith("WAIV-")
            assert (await client.post("/api/v1/projects/am-e2e/vault/verify")).json()["passed"] is True
            _mark("POST", "/api/v1/projects/{project_id}/vault/verify")

            # Compile all 11 targets
            compiled_all = (
                await client.post("/api/v1/projects/am-e2e/storage/compile", json={"target_id": "all"})
            ).json()
            _mark("POST", "/api/v1/projects/{project_id}/storage/compile")
            assert len(compiled_all) == 11
            manifests = (await client.get("/api/v1/projects/am-e2e/storage/targets")).json()
            _mark("GET", "/api/v1/projects/{project_id}/storage/targets")
            assert len(manifests) == 11

            assert (
                await client.post("/api/v1/projects/am-e2e/storage/migrate", json={"target_id": "duckdb_local"})
            ).json()["verified"] is True
            _mark("POST", "/api/v1/projects/{project_id}/storage/migrate")

            # --- Wave 2 Evolution (W001 -> W002) ---
            copy_public_am_wave2(ws)
            for w2_kind, w2_file in (
                ("parquet", "public_am/wave2_eu_custody_lei_and_drift.parquet"),
                ("csv", "public_am/wave2_bitemporal_nav_restatements.csv"),
                ("csv", "public_am/wave2_ambiguous_otc_collateral.csv"),
                ("csv", "public_am/prime_allocations_w2.csv"),
            ):
                assert (
                    await client.post(
                        "/api/v1/projects/am-e2e/sources",
                        json={"kind": w2_kind, "uri_or_filename": w2_file},
                    )
                ).status_code == 200

            w2_idx = (await client.get("/api/v1/projects/am-e2e/evidence/tables/index")).json()
            badges = {item["table_name"]: item["badge"] for item in w2_idx}
            assert badges["wave2_eu_custody_lei_and_drift"] == "NEW"
            assert badges["holdings_13f"] == "UNCHANGED"

            # Stale OCC -> 409 REVISION_CONFLICT
            _assert_problem(
                await client.post(
                    "/api/v1/projects/am-e2e/ontology/commit",
                    json={
                        "operations": [
                            AddConcept(
                                concept_uri="urn:asset_mgmt:OtcCollateral",
                                concept_name="OtcCollateral",
                                description="OTC collateral",
                                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                            ).model_dump(mode="json")
                        ],
                        "base_revision_id": proj["active_ontology_rev_id"],
                        "rationale": "Stale OCC test",
                    },
                ),
                status=409,
                error_code="REVISION_CONFLICT",
            )
            # Commit Wave 2 ontology (v0.2.0)
            w2_srcs = (await client.get("/api/v1/projects/am-e2e/sources")).json()
            w2_eu_src = next(s for s in w2_srcs if "wave2_eu" in s["uri_or_filename"])
            w2_nav_src = next(s for s in w2_srcs if "bitemporal_nav" in s["uri_or_filename"])
            w2_otc_src = next(s for s in w2_srcs if "otc_collateral" in s["uri_or_filename"])
            w2_alloc_src = next(s for s in w2_srcs if "prime_allocations" in s["uri_or_filename"])
            rev2 = (
                await client.post(
                    "/api/v1/projects/am-e2e/ontology/commit",
                    json={
                        "operations": [
                            AddConcept(
                                concept_uri="urn:asset_mgmt:CustodyAccount",
                                concept_name="CustodyAccount",
                                description="EU custody account",
                                key_authority_tier="TIER_2_ENTERPRISE_SOR",
                                table_bindings=[
                                    ConceptTableBinding(
                                        source_id=w2_eu_src["source_id"],
                                        table_id=f"{w2_eu_src['source_id']}:wave2_eu_custody_lei_and_drift",
                                        col_name="lei_code",
                                    )
                                ],
                            ).model_dump(mode="json"),
                            AddConcept(
                                concept_uri="urn:asset_mgmt:SecurityIsin",
                                concept_name="Security Isin",
                                description="Global ISIN security identifier",
                                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                                table_bindings=[
                                    ConceptTableBinding(
                                        source_id=w2_eu_src["source_id"],
                                        table_id=f"{w2_eu_src['source_id']}:wave2_eu_custody_lei_and_drift",
                                        col_name="isin",
                                    )
                                ],
                            ).model_dump(mode="json"),
                            AddConcept(
                                concept_uri="urn:asset_mgmt:FundNav",
                                concept_name="Fund Nav",
                                description="Bitemporal fund NAV valuation",
                                key_authority_tier="TIER_2_ENTERPRISE_SOR",
                                table_bindings=[
                                    ConceptTableBinding(
                                        source_id=w2_nav_src["source_id"],
                                        table_id=f"{w2_nav_src['source_id']}:wave2_bitemporal_nav_restatements",
                                        col_name="fund_code",
                                    )
                                ],
                            ).model_dump(mode="json"),
                            AddConcept(
                                concept_uri="urn:asset_mgmt:OtcCollateral",
                                concept_name="Otc Collateral",
                                description="OTC derivative collateral",
                                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                                table_bindings=[
                                    ConceptTableBinding(
                                        source_id=w2_otc_src["source_id"],
                                        table_id=f"{w2_otc_src['source_id']}:wave2_ambiguous_otc_collateral",
                                        col_name="uti_id",
                                    )
                                ],
                            ).model_dump(mode="json"),
                            AddConcept(
                                concept_uri="urn:asset_mgmt:PrimeAllocation",
                                concept_name="Prime Allocation",
                                description="Prime brokerage block allocation",
                                key_authority_tier="TIER_2_ENTERPRISE_SOR",
                                table_bindings=[
                                    ConceptTableBinding(
                                        source_id=w2_alloc_src["source_id"],
                                        table_id=f"{w2_alloc_src['source_id']}:prime_allocations_w2",
                                        col_name="block_id",
                                    )
                                ],
                            ).model_dump(mode="json"),
                        ],
                        "base_revision_id": rev1["revision_id"],
                        "rationale": "Commit Wave 2 concepts.",
                    },
                )
            ).json()
            assert rev2["ontology_version"] == "0.2.0"

            # Legacy Wave-2 test fixture wave2_bitemporal_nav_restatements.csv has duplicate
            # (fund_code, share_class, as_of_date, applied_dts) rows tested as STANDARD sat_nav_bitemp.
            assert (
                await client.patch(
                    "/api/v1/projects/am-e2e/settings",
                    json={"real_row_proving": False},
                )
            ).status_code == 200
            wave_mode["wave"] = 2
            rid_w2 = (
                await client.post("/api/v1/projects/am-e2e/runs", json={"policy": "balanced"})
            ).json()["run_id"]
            await wait_run_via_sse(client, f"/api/v1/projects/am-e2e/runs/{rid_w2}/events")
            assert (await client.get(f"/api/v1/projects/am-e2e/runs/{rid_w2}")).json()["status"] == "HOLD_CHECKPOINTED"

            # Resume L3 Hold via POST .../decisions
            assert (
                await client.post(
                    f"/api/v1/projects/am-e2e/runs/{rid_w2}/decisions",
                    json={"answers": {"q-otc-nk": "uti_id"}},
                )
            ).status_code == 202
            _mark("POST", "/api/v1/projects/{project_id}/runs/{rid}/decisions")
            await wait_run_via_sse(client, f"/api/v1/projects/am-e2e/runs/{rid_w2}/events")

            # Horizon steering + controls
            st_dec = (await client.get(f"/api/v1/projects/am-e2e/runs/{rid_w2}")).json()
            if st_dec["status"] not in ("COMPLETED", "HOLD_CHECKPOINTED"):
                att = (await client.get(f"/api/v1/projects/am-e2e/runs/{rid_w2}/attempts")).json()
                raise AssertionError(f"Wave 2 unexpected status: {st_dec}\nattempts={att}")
            assert (
                await client.post(
                    f"/api/v1/projects/am-e2e/runs/{rid_w2}/horizon",
                    json={"action": "accept", "notes": "Accept sal_issuer mapping"},
                )
            ).status_code == 202
            _mark("POST", "/api/v1/projects/{project_id}/runs/{rid}/horizon")
            await wait_run_via_sse(client, f"/api/v1/projects/am-e2e/runs/{rid_w2}/events")
            st_hor = (await client.get(f"/api/v1/projects/am-e2e/runs/{rid_w2}")).json()
            gv_hor = (await client.get(f"/api/v1/projects/am-e2e/runs/{rid_w2}/guided-view")).json()
            assert st_hor["status"] == "COMPLETED", (st_hor, gv_hor)

            assert (
                await client.post(
                    f"/api/v1/projects/am-e2e/runs/{rid_w2}/controls",
                    json={"action": "hint", "hint": "Verified Wave 2"},
                )
            ).status_code == 200
            _mark("POST", "/api/v1/projects/{project_id}/runs/{rid}/controls")

            # Migrate V002 + Regenerate + Query bitemporal nav_per_share + SQL guard
            assert (
                await client.post("/api/v1/projects/am-e2e/storage/migrate", json={"target_id": "duckdb_local"})
            ).json()["wave_number"] == 2
            assert (
                await client.post("/api/v1/projects/am-e2e/storage/regenerate", json={"target_id": "duckdb_local"})
            ).json()["verified"] is True
            _mark("POST", "/api/v1/projects/{project_id}/storage/regenerate")

            nav_q = (
                await client.post(
                    "/api/v1/projects/am-e2e/storage/query",
                    json={
                        "sql": (
                            "SELECT s.applied_dts, s.nav_per_share "
                            "FROM raw_vault.hub_fund_nav AS h "
                            "INNER JOIN raw_vault.sat_nav_bitemp AS s ON s.hk_fund_nav = h.hk_fund_nav "
                            "WHERE h.fund_code = 'AM-CORE-EQ' AND h.share_class = 'INST_A' "
                            "AND h.as_of_date = DATE '2026-10-20' ORDER BY s.applied_dts ASC"
                        )
                    },
                )
            ).json()
            _mark("POST", "/api/v1/projects/{project_id}/storage/query")
            assert [r[1] for r in nav_q["rows"]] == [216.28, 245.0, 156.25]

            for bad_sql in ("DROP TABLE raw_vault.hub_filer", "SELECT * FROM read_csv_auto('x.csv')"):
                _assert_problem(
                    await client.post(
                        "/api/v1/projects/am-e2e/storage/query",
                        json={"sql": bad_sql},
                    ),
                    status=400,
                    error_code="SYNTAX_INVALID_ARGUMENT",
                )

            # Export bundle + import + delete project + delete mcp server
            r_bundle = await client.get("/api/v1/projects/am-e2e/bundle?mode=core_project_state")
            _mark("GET", "/api/v1/projects/{project_id}/bundle")
            assert r_bundle.status_code == 200
            r_imp = await client.post(
                "/api/v1/projects/import",
                files={"file": ("am.core.opendv.zip", r_bundle.content, "application/zip")},
                data={"conflict": "rename"},
            )
            _mark("POST", "/api/v1/projects/import")
            assert r_imp.status_code == 200

            assert (await client.delete("/api/v1/projects/am-e2e-copy")).status_code == 204
            _mark("DELETE", "/api/v1/projects/{project_id}")
            assert (await client.delete("/api/v1/mcp/servers/fake-fibo-mcp")).status_code == 204
            _mark("DELETE", "/api/v1/mcp/servers/{server_id}")

            # --- scale_200 latency < 300ms ---
            await client.post(
                "/api/v1/projects",
                json={"project_id": "scale-200", "domain_namespace": "finance"},
            )
            await client.post(
                "/api/v1/projects/scale-200/sources",
                json={"kind": "sqlite", "uri_or_filename": os.fspath(scale_db)},
            )
            t0 = time.perf_counter()
            r_scale = await client.get("/api/v1/projects/scale-200/evidence/tables/index")
            elapsed = time.perf_counter() - t0
            assert r_scale.status_code == 200
            assert len(r_scale.json()) == 220
            assert elapsed < 0.30, f"scale_200 tables/index took {elapsed:.3f}s (>= 0.30s)"

            first_tid = r_scale.json()[0]["table_id"]
            assert (
                await client.get(f"/api/v1/projects/scale-200/evidence/tables/{first_tid}")
            ).status_code == 200

            # Verify zero secret leakage across endpoints
            assert secret_val not in json.dumps((await client.get("/api/v1/settings")).json())
            assert secret_val not in json.dumps((await client.get("/api/v1/projects/am-e2e/sources")).json())

        missing = expected_ops - called_ops
        assert not missing, f"Uncovered /api/v1 routes: {sorted(missing)}"
    finally:
        fake_mcp.stop()


@pytest.mark.asyncio
async def test_remediation_step4_step6_stream_seq_monotonicity(tmp_path: Path) -> None:
    copy_public_am_wave1(tmp_path / "ws")

    class _StreamingHarness(DeterministicMockHarness):
        async def run_turn(
            self,
            request: Any,
            *,
            event_sink: Any = None,
            scout_dispatcher: Any = None,
        ) -> Any:
            if event_sink is not None:
                event_sink.emit(
                    RunStreamEvent(
                        seq=1,
                        run_id=request.run_id,
                        ts="2026-11-24T00:00:00Z",
                        event_type="SESSION_START",
                        step=request.step,
                        payload={"agent_id": request.agent_id},
                    )
                )
                for idx in range(10):
                    event_sink.emit(
                        RunStreamEvent(
                            seq=idx + 2,
                            run_id=request.run_id,
                            ts="2026-11-24T00:00:00Z",
                            event_type="TEXT_DELTA",
                            step=request.step,
                            payload={"delta_text": f"chunk-{idx}"},
                        )
                    )
            return await super().run_turn(
                request, event_sink=event_sink, scout_dispatcher=scout_dispatcher
            )

    def _sel(req: Any) -> MockTurnScript:
        if req.agent_id == "ontology-first-mate":
            if req.metadata.get("discovery_mode") == "full_rebuild":
                return MockTurnScript(
                    structured_output={
                        "proposed_ops": [],
                        "clarification_questions": [
                            ClarificationQuestion(
                                question_id="q-seq-hold",
                                concept_name="Filer",
                                title="Confirm CIK",
                                prompt="Confirm CIK?",
                                sample_rows=["0001"],
                                impact_if_locked="Locks BK.",
                                recommended_answer="Yes",
                            ).model_dump(mode="json")
                        ],
                        "summary": "Streamed Step 4 hold turn",
                    }
                )
            return MockTurnScript(
                structured_output={
                    "proposed_ops": [],
                    "clarification_questions": [],
                    "summary": "Streamed Step 4 turn",
                }
            )
        if req.agent_id == "ship-vault":
            profs = load_project_profiles(svc, "p-seq")
            return MockTurnScript(structured_output=build_wave1_am_draft("p-seq", "0.1.0", profs))
        if req.agent_id == "dv-semantic-critic":
            return MockTurnScript(
                structured_output=SemanticCriticVerdict(
                    score=95,
                    accepted_waiver_ids=["WAIV-AP004", "WAIV-AP004-NAV"],
                )
            )
        if req.agent_id == "dv-horizon-critic":
            return MockTurnScript(
                structured_output=HorizonCriticVerdict(score=93, classification="REASONABLY_STABLE")
            )
        return MockTurnScript(text_output="{}")

    svc = _make_svc(tmp_path, harness=_StreamingHarness(selector=_sel))
    svc.init_project(ProjectInitReq(project_id="p-seq", domain_namespace="asset_mgmt"))
    src = svc.attach_source(
        "p-seq",
        SourceAttachReq(kind="sqlite", uri_or_filename="public_am/am_sec_treasury_ibor.sqlite"),
    )
    pq = svc.attach_source(
        "p-seq",
        SourceAttachReq(kind="parquet", uri_or_filename="public_am/am_esg_stewardship_proxy.parquet"),
    )
    md = svc.attach_source(
        "p-seq",
        SourceAttachReq(kind="markdown", uri_or_filename="public_am/am_stewardship_domain_notes.md"),
    )
    app = create_app(svc)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        # Step 4 SSE receives SESSION_START + 10 TEXT_DELTA + RUN_COMPLETED with strictly 1..N seq
        r_s4 = await client.post("/api/v1/projects/p-seq/ontology/construct", json={})
        assert r_s4.status_code == 202
        rid4 = r_s4.json()["run_id"]
        frames4 = await wait_run_via_sse(
            client, f"/api/v1/projects/p-seq/ontology/runs/{rid4}/events"
        )
        ev_types4 = [f.get("event") for f in frames4]
        assert "SESSION_START" in ev_types4
        assert ev_types4.count("TEXT_DELTA") == 10
        assert "RUN_COMPLETED" in ev_types4
        log4 = svc.get_run_logs("p-seq", rid4, since_seq=0)
        assert [ev.seq for ev in log4.events] == list(range(1, len(log4.events) + 1))
        assert (
            await client.get(f"/api/v1/projects/p-seq/ontology/runs/{rid4}")
        ).json()["last_seq"] == len(log4.events)

        # Step 4 Hold SSE receives SESSION_START + 10 TEXT_DELTA + HOLD_CHECKPOINTED with 1..N seq
        r_s4h = await client.post(
            "/api/v1/projects/p-seq/ontology/construct",
            json={"discovery_mode": "full_rebuild"},
        )
        assert r_s4h.status_code == 202
        rid4h = r_s4h.json()["run_id"]
        frames4h = await wait_run_via_sse(
            client, f"/api/v1/projects/p-seq/ontology/runs/{rid4h}/events"
        )
        ev_types4h = [f.get("event") for f in frames4h]
        assert "SESSION_START" in ev_types4h
        assert ev_types4h.count("TEXT_DELTA") == 10
        assert "HOLD_CHECKPOINTED" in ev_types4h
        log4h = svc.get_run_logs("p-seq", rid4h, since_seq=0)
        assert [ev.seq for ev in log4h.events] == list(range(1, len(log4h.events) + 1))
        assert (
            await client.get(f"/api/v1/projects/p-seq/ontology/runs/{rid4h}")
        ).json()["last_seq"] == len(log4h.events)

        # Item 6: Step 6 controls rejects step4 run_id with 404 RESOURCE_NOT_FOUND
        _assert_problem(
            await client.post(
                f"/api/v1/projects/p-seq/runs/{rid4}/controls",
                json={"action": "pause"},
            ),
            status=404,
            error_code="RESOURCE_NOT_FOUND",
        )

        # Commit W1 ontology & run Step 6 with streaming harness
        proj = svc.get_project("p-seq")
        assert proj.active_ontology_rev_id is not None
        md_q = (
            svc.get_document_pages("p-seq", md.source_id, page_start=1, page_end=1)[0]
            .masked_text.splitlines()[0]
            .strip()
        )
        from opendv.models.wire_dtos_discovery import OntologyCommitReq

        svc.commit_ontology(
            "p-seq",
            OntologyCommitReq(
                operations=build_wave1_commit_ops(
                    src.source_id, pq.source_id, md.source_id, md_q
                ),  # type: ignore[arg-type]
                base_revision_id=proj.active_ontology_rev_id,
                rationale="Commit W1 ontology for seq monotonicity test.",
            ),
        )

        r_s6 = await client.post("/api/v1/projects/p-seq/runs", json={"policy": "balanced"})
        assert r_s6.status_code == 202
        rid6 = r_s6.json()["run_id"]
        frames6 = await wait_run_via_sse(client, f"/api/v1/projects/p-seq/runs/{rid6}/events")
        ev_types6 = [f.get("event") for f in frames6]
        assert "SESSION_START" in ev_types6
        assert ev_types6.count("TEXT_DELTA") >= 10
        assert "GATE_CHECK_STREAM" in ev_types6
        assert "ATTEMPT_SNAPSHOT" in ev_types6
        assert "RUN_COMPLETED" in ev_types6
        log6 = svc.get_run_logs("p-seq", rid6, since_seq=0)
        assert [ev.seq for ev in log6.events] == list(range(1, len(log6.events) + 1))
        assert (
            await client.get(f"/api/v1/projects/p-seq/runs/{rid6}")
        ).json()["last_seq"] == len(log6.events)


@pytest.mark.asyncio
async def test_remediation_step6_pause_resume_and_zero_await_crash(tmp_path: Path) -> None:
    copy_public_am_wave1(tmp_path / "ws")
    attempt1_started = asyncio.Event()
    attempt1_release = asyncio.Event()
    mode: dict[str, str] = {"phase": "pause_attempt1"}

    class _Step6RemediationHarness(DeterministicMockHarness):
        async def run_turn(
            self,
            request: Any,
            *,
            event_sink: Any = None,
            scout_dispatcher: Any = None,
        ) -> Any:
            if mode["phase"] == "sync_boom" and request.agent_id == "ship-vault":
                raise RuntimeError("boom_zero_await")
            if (
                mode["phase"] == "pause_attempt1"
                and request.agent_id == "ship-vault"
                and request.attempt == 1
            ):
                attempt1_started.set()
                await attempt1_release.wait()
            return await super().run_turn(
                request, event_sink=event_sink, scout_dispatcher=scout_dispatcher
            )

    def _sel(req: Any) -> MockTurnScript:
        if req.agent_id == "ship-vault":
            profs = load_project_profiles(svc, "p-s6rem")
            if mode["phase"] == "pause_attempt1":
                # Empty hubs/triage causes V1-V6 failure so attempt 2 is entered
                from opendv.models.vault_ir import DraftVaultIR

                return MockTurnScript(
                    structured_output=DraftVaultIR(
                        project_id="p-s6rem",
                        ontology_version="0.1.0",
                        wave_number=1,
                        hubs=[],
                        links=[],
                        satellites=[],
                        column_triage=[],
                    )
                )
            return MockTurnScript(
                structured_output=build_wave1_am_draft("p-s6rem", "0.1.0", profs)
            )
        if req.agent_id == "dv-semantic-critic":
            return MockTurnScript(
                structured_output=SemanticCriticVerdict(
                    score=95,
                    accepted_waiver_ids=["WAIV-AP004", "WAIV-AP004-NAV"],
                )
            )
        if req.agent_id == "dv-horizon-critic":
            return MockTurnScript(
                structured_output=HorizonCriticVerdict(score=93, classification="REASONABLY_STABLE")
            )
        return MockTurnScript(text_output="{}")

    svc = _make_svc(tmp_path, harness=_Step6RemediationHarness(selector=_sel))
    svc.init_project(ProjectInitReq(project_id="p-s6rem", domain_namespace="asset_mgmt"))
    src = svc.attach_source(
        "p-s6rem",
        SourceAttachReq(kind="sqlite", uri_or_filename="public_am/am_sec_treasury_ibor.sqlite"),
    )
    pq = svc.attach_source(
        "p-s6rem",
        SourceAttachReq(kind="parquet", uri_or_filename="public_am/am_esg_stewardship_proxy.parquet"),
    )
    md = svc.attach_source(
        "p-s6rem",
        SourceAttachReq(kind="markdown", uri_or_filename="public_am/am_stewardship_domain_notes.md"),
    )
    proj = svc.get_project("p-s6rem")
    assert proj.active_ontology_rev_id is not None
    md_q = (
        svc.get_document_pages("p-s6rem", md.source_id, page_start=1, page_end=1)[0]
        .masked_text.splitlines()[0]
        .strip()
    )
    from opendv.models.wire_dtos_discovery import OntologyCommitReq

    svc.commit_ontology(
        "p-s6rem",
        OntologyCommitReq(
            operations=build_wave1_commit_ops(
                src.source_id, pq.source_id, md.source_id, md_q
            ),  # type: ignore[arg-type]
            base_revision_id=proj.active_ontology_rev_id,
            rationale="Commit W1 ontology for Step 6 pause/crash tests.",
        ),
    )
    app = create_app(svc)

    # 1. Step 6 multi-attempt pause -> HOLD_CHECKPOINTED -> decisions({}) -> COMPLETED
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        r_start = await client.post("/api/v1/projects/p-s6rem/runs", json={"policy": "balanced"})
        assert r_start.status_code == 202
        rid = r_start.json()["run_id"]
        await asyncio.wait_for(attempt1_started.wait(), timeout=5.0)
        r_pause = await client.post(
            f"/api/v1/projects/p-s6rem/runs/{rid}/controls",
            json={"action": "pause"},
        )
        assert r_pause.status_code == 200
        assert r_pause.json()["status"] == "HOLD_REQUESTED"
        attempt1_release.set()

        frames_hold = await wait_run_via_sse(client, f"/api/v1/projects/p-s6rem/runs/{rid}/events")
        assert any(f.get("event") == "HOLD_CHECKPOINTED" for f in frames_hold)
        st_hold = (await client.get(f"/api/v1/projects/p-s6rem/runs/{rid}")).json()
        assert st_hold["status"] == "HOLD_CHECKPOINTED"
        assert st_hold["pending_questions"] == []

        mode["phase"] = "healthy"
        r_res = await client.post(
            f"/api/v1/projects/p-s6rem/runs/{rid}/decisions",
            json={"answers": {}},
        )
        assert r_res.status_code == 202
        frames_done = await wait_run_via_sse(client, f"/api/v1/projects/p-s6rem/runs/{rid}/events")
        assert any(f.get("event") == "RUN_COMPLETED" for f in frames_done)
        st_done = (await client.get(f"/api/v1/projects/p-s6rem/runs/{rid}")).json()
        assert st_done["status"] == "COMPLETED"

    # 2. Zero-await RuntimeError inside build_vault never leaves RUNNING stranded
    mode["phase"] = "sync_boom"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://127.0.0.1:8420",
    ) as crash_client:
        r_boom = await crash_client.post("/api/v1/projects/p-s6rem/runs", json={"policy": "fast"})
        assert r_boom.status_code == 500
        runs_after = (await crash_client.get("/api/v1/projects/p-s6rem/runs?step=step6")).json()
        assert runs_after[0]["status"] == "FAILED"
        boom_rid = runs_after[0]["run_id"]
        assert (
            await crash_client.get(f"/api/v1/projects/p-s6rem/runs/{boom_rid}")
        ).json()["status"] == "FAILED"
        boom_logs = (
            await crash_client.get(f"/api/v1/projects/p-s6rem/runs/{boom_rid}/logs")
        ).json()
        assert any(ev["event_type"] == "RUN_FAILED" for ev in boom_logs["events"])

        # Lease was released so subsequent run succeeds
        mode["phase"] = "healthy"
        r_ok = await crash_client.post("/api/v1/projects/p-s6rem/runs", json={"policy": "fast"})
        assert r_ok.status_code == 202
        ok_rid = r_ok.json()["run_id"]
        await wait_run_via_sse(crash_client, f"/api/v1/projects/p-s6rem/runs/{ok_rid}/events")
        assert (
            await crash_client.get(f"/api/v1/projects/p-s6rem/runs/{ok_rid}")
        ).json()["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_remediation_security_headers_scheme_405_and_assets_404(tmp_path: Path) -> None:
    svc = _make_svc(tmp_path)
    static_dir = tmp_path / "static_sec"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    (static_dir / "secret.txt").write_text("TOP_SECRET", encoding="utf-8")
    (static_dir / "assets" / "app.js").write_text("console.log(1);", encoding="utf-8")
    app = create_app(svc, static_dir=static_dir)

    async def _raw_req(
        method: str,
        path: str,
        raw_headers: list[tuple[bytes, bytes]],
        body: bytes = b"",
    ) -> tuple[int, dict[str, Any]]:
        sent: list[dict[str, Any]] = []
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": raw_headers,
            "client": ("127.0.0.1", 55555),
            "server": ("127.0.0.1", 8420),
            "app": app,
        }

        async def _recv() -> dict[str, Any]:
            return {"type": "http.request", "body": body, "more_body": False}

        async def _send(msg: dict[str, Any]) -> None:
            sent.append(msg)

        await app(scope, _recv, _send)
        start = next(m for m in sent if m["type"] == "http.response.start")
        raw_body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return int(start["status"]), json.loads(raw_body.decode("utf-8"))

    # Duplicate Origin, Sec-Fetch-Site, Host, Origin scheme mismatch -> 403
    st_dup_orig, b_dup_orig = await _raw_req(
        "GET",
        "/api/v1/health",
        [
            (b"host", b"127.0.0.1:8420"),
            (b"origin", b"http://127.0.0.1:8420"),
            (b"origin", b"http://evil.example"),
        ],
    )
    assert st_dup_orig == 403 and b_dup_orig["error_code"] == "LOOPBACK_ORIGIN_FORBIDDEN"

    st_dup_sfs, b_dup_sfs = await _raw_req(
        "GET",
        "/api/v1/health",
        [
            (b"host", b"127.0.0.1:8420"),
            (b"sec-fetch-site", b"same-origin"),
            (b"sec-fetch-site", b"cross-site"),
        ],
    )
    assert st_dup_sfs == 403 and b_dup_sfs["error_code"] == "LOOPBACK_ORIGIN_FORBIDDEN"

    st_dup_host, b_dup_host = await _raw_req(
        "GET",
        "/api/v1/health",
        [(b"host", b"127.0.0.1:8420"), (b"host", b"evil.example:8420")],
    )
    assert st_dup_host == 403 and b_dup_host["error_code"] == "LOOPBACK_ORIGIN_FORBIDDEN"

    st_scheme, b_scheme = await _raw_req(
        "GET",
        "/api/v1/health",
        [(b"host", b"127.0.0.1:8420"), (b"origin", b"https://127.0.0.1:8420")],
    )
    assert st_scheme == 403 and b_scheme["error_code"] == "LOOPBACK_ORIGIN_FORBIDDEN"

    # Duplicate Content-Type -> 415; duplicate Content-Length -> 400 or 413 if oversized
    st_dup_ct, b_dup_ct = await _raw_req(
        "POST",
        "/api/v1/projects",
        [
            (b"host", b"127.0.0.1:8420"),
            (b"content-type", b"application/json"),
            (b"content-type", b"text/plain"),
        ],
        body=b"{}",
    )
    assert st_dup_ct == 415 and b_dup_ct["error_code"] == "UNSUPPORTED_MEDIA_TYPE"

    st_dup_cl, b_dup_cl = await _raw_req(
        "POST",
        "/api/v1/projects",
        [
            (b"host", b"127.0.0.1:8420"),
            (b"content-type", b"application/json"),
            (b"content-length", b"2"),
            (b"content-length", b"10"),
        ],
        body=b"{}",
    )
    assert st_dup_cl == 400 and b_dup_cl["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    st_dup_cl_big, b_dup_cl_big = await _raw_req(
        "POST",
        "/api/v1/projects",
        [
            (b"host", b"127.0.0.1:8420"),
            (b"content-type", b"application/json"),
            (b"content-length", b"0"),
            (b"content-length", b"262144001"),
        ],
        body=b"{}",
    )
    assert st_dup_cl_big == 413 and b_dup_cl_big["error_code"] == "ARCHIVE_SIZE_LIMIT_EXCEEDED"

    # 405 Method Not Allowed emits ProblemDetails.status == 405; /assets/%2e%2e/ -> 404
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        r_405 = await client.put("/api/v1/health")
        _assert_problem(r_405, status=405, error_code="SYNTAX_INVALID_ARGUMENT")
        _assert_problem(
            await client.get("/assets/%2e%2e/secret.txt"),
            status=404,
            error_code="RESOURCE_NOT_FOUND",
        )
        _assert_problem(
            await client.get("/assets/../../secret.txt"),
            status=404,
            error_code="RESOURCE_NOT_FOUND",
        )


@pytest.mark.asyncio
async def test_remediation_s6_05_s6_06_resume_payload_and_step4_isolation(tmp_path: Path) -> None:
    copy_public_am_wave1(tmp_path / "ws")
    phase: dict[str, Any] = {"mode": "s6_05"}
    a1_started = asyncio.Event()
    a1_release = asyncio.Event()
    observed_ship: list[tuple[int, int, int, int, list[str]]] = []
    observed_v8: list[tuple[int, int, int]] = []

    class _SyncHarness(DeterministicMockHarness):
        async def run_turn(
            self,
            request: Any,
            *,
            event_sink: Any = None,
            scout_dispatcher: Any = None,
        ) -> Any:
            if request.agent_id == "ship-vault":
                prompt_data = json.loads(request.prompt)
                observed_ship.append(
                    (
                        request.attempt,
                        request.budget.max_turns,
                        request.budget.max_tokens,
                        request.budget.timeout_seconds,
                        list(prompt_data.get("hints", [])),
                    )
                )
                if request.attempt == 1 and not a1_started.is_set():
                    a1_started.set()
                    await a1_release.wait()
            elif request.agent_id == "dv-horizon-critic":
                observed_v8.append(
                    (
                        request.budget.max_turns,
                        request.budget.max_tokens,
                        request.budget.timeout_seconds,
                    )
                )
            return await super().run_turn(
                request, event_sink=event_sink, scout_dispatcher=scout_dispatcher
            )

    def _sel(req: Any) -> MockTurnScript:
        if req.agent_id == "ontology-first-mate":
            return MockTurnScript(
                structured_output={
                    "proposed_ops": [],
                    "clarification_questions": [
                        ClarificationQuestion(
                            question_id="q-s4-iso",
                            concept_name="Filer",
                            title="Confirm CIK",
                            prompt="Use SEC CIK?",
                            sample_rows=["0001"],
                            impact_if_locked="Locks BK.",
                            recommended_answer="Yes",
                        ).model_dump(mode="json")
                    ],
                    "summary": "Hold step4",
                }
            )
        if req.agent_id == "ship-vault":
            profs = load_project_profiles(svc, "p-iso")
            if phase["mode"] == "s6_05" and req.attempt == 1:
                from opendv.models.vault_ir import DraftVaultIR

                return MockTurnScript(
                    structured_output=DraftVaultIR(
                        project_id="p-iso",
                        ontology_version="0.1.0",
                        wave_number=1,
                        hubs=[],
                        links=[],
                        satellites=[],
                        column_triage=[],
                    )
                )
            if phase["mode"] == "s6_05" and req.attempt == 2 and not phase.get("clar_done"):
                phase["clar_done"] = True
                return MockTurnScript(
                    structured_output=build_wave1_am_draft("p-iso", "0.1.0", profs).model_copy(
                        update={
                            "pending_clarifications": [
                                ClarificationQuestion(
                                    question_id="q-s6-05",
                                    concept_name="Filer",
                                    title="Confirm key",
                                    prompt="Use filer_cik?",
                                    sample_rows=["0001"],
                                    impact_if_locked="Locks key.",
                                    recommended_answer="Yes",
                                )
                            ]
                        }
                    )
                )
            return MockTurnScript(structured_output=build_wave1_am_draft("p-iso", "0.1.0", profs))
        if req.agent_id == "dv-semantic-critic":
            return MockTurnScript(
                structured_output=SemanticCriticVerdict(
                    score=95,
                    accepted_waiver_ids=["WAIV-AP004", "WAIV-AP004-NAV"],
                )
            )
        if req.agent_id == "dv-horizon-critic":
            if not phase.get("v8_low_once"):
                phase["v8_low_once"] = True
                return MockTurnScript(
                    structured_output=HorizonCriticVerdict(
                        score=70, classification="HUMAN_REVIEW_REQUIRED"
                    )
                )
            if not phase.get("v8_low_twice"):
                phase["v8_low_twice"] = True
                return MockTurnScript(
                    structured_output=HorizonCriticVerdict(
                        score=72, classification="HUMAN_REVIEW_REQUIRED"
                    )
                )
            return MockTurnScript(
                structured_output=HorizonCriticVerdict(score=93, classification="REASONABLY_STABLE")
            )
        return MockTurnScript(text_output="{}")

    svc = _make_svc(tmp_path, harness=_SyncHarness(selector=_sel))
    svc.init_project(ProjectInitReq(project_id="p-iso", domain_namespace="asset_mgmt"))
    src = svc.attach_source(
        "p-iso",
        SourceAttachReq(kind="sqlite", uri_or_filename="public_am/am_sec_treasury_ibor.sqlite"),
    )
    pq = svc.attach_source(
        "p-iso",
        SourceAttachReq(kind="parquet", uri_or_filename="public_am/am_esg_stewardship_proxy.parquet"),
    )
    md = svc.attach_source(
        "p-iso",
        SourceAttachReq(kind="markdown", uri_or_filename="public_am/am_stewardship_domain_notes.md"),
    )
    proj = svc.get_project("p-iso")
    assert proj.active_ontology_rev_id is not None
    md_q = (
        svc.get_document_pages("p-iso", md.source_id, page_start=1, page_end=1)[0]
        .masked_text.splitlines()[0]
        .strip()
    )
    from opendv.models.wire_dtos_discovery import OntologyCommitReq

    svc.commit_ontology(
        "p-iso",
        OntologyCommitReq(
            operations=build_wave1_commit_ops(
                src.source_id, pq.source_id, md.source_id, md_q
            ),  # type: ignore[arg-type]
            base_revision_id=proj.active_ontology_rev_id,
            rationale="Commit W1 ontology for S6-05/S6-06 & Step 4 isolation tests.",
        ),
    )
    app = create_app(svc)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        # 1) Step 4 isolation parity on Step 6 routes (preserving unified GET /runs/{rid} & /logs)
        r_s4 = await client.post("/api/v1/projects/p-iso/ontology/construct", json={})
        assert r_s4.status_code == 202
        rid4 = r_s4.json()["run_id"]
        await wait_run_via_sse(client, f"/api/v1/projects/p-iso/ontology/runs/{rid4}/events")

        before_states = svc.state_manager.list_events_or_receipts(
            "p-iso", record_kind="run_state", run_id=rid4
        )
        before_hor = svc.state_manager.list_revisions("p-iso", rev_kind="horizon_assumption")

        _assert_problem(
            await client.post(
                f"/api/v1/projects/p-iso/runs/{rid4}/decisions",
                json={"answers": {"q-s4-iso": "Yes"}},
            ),
            status=404,
            error_code="RESOURCE_NOT_FOUND",
        )
        _assert_problem(
            await client.post(
                f"/api/v1/projects/p-iso/runs/{rid4}/horizon",
                json={"action": "accept", "notes": "wrong step"},
            ),
            status=404,
            error_code="RESOURCE_NOT_FOUND",
        )
        _assert_problem(
            await client.get(f"/api/v1/projects/p-iso/runs/{rid4}/events"),
            status=404,
            error_code="RESOURCE_NOT_FOUND",
        )
        _assert_problem(
            await client.get(f"/api/v1/projects/p-iso/runs/{rid4}/attempts"),
            status=404,
            error_code="RESOURCE_NOT_FOUND",
        )
        assert (
            svc.state_manager.list_revisions("p-iso", rev_kind="horizon_assumption") == before_hor
        )
        assert (
            len(
                svc.state_manager.list_events_or_receipts(
                    "p-iso", record_kind="run_state", run_id=rid4
                )
            )
            == len(before_states)
        )
        r_unified = await client.get(f"/api/v1/projects/p-iso/runs/{rid4}")
        assert r_unified.status_code == 200 and r_unified.json()["step"] == "step4"
        assert (await client.get(f"/api/v1/projects/p-iso/runs/{rid4}/logs")).status_code == 200

        # Finish step4 hold cleanly so no active lease remains
        await client.post(
            f"/api/v1/projects/p-iso/ontology/runs/{rid4}/answers",
            json={"answers": {"q-s4-iso": "Yes"}},
        )
        await wait_run_via_sse(client, f"/api/v1/projects/p-iso/ontology/runs/{rid4}/events")

        # 2) S6-05 hold preserves pre-consumed raise-budget, hints, and accept_best_requested
        r_s6_a = await client.post("/api/v1/projects/p-iso/runs", json={"policy": "balanced"})
        assert r_s6_a.status_code == 202
        rid_a = r_s6_a.json()["run_id"]
        await asyncio.wait_for(a1_started.wait(), timeout=5.0)
        for ctrl in (
            {
                "action": "raise-budget",
                "extra_turns": 6,
                "extra_tokens": 1200,
                "extra_timeout_seconds": 45,
            },
            {"action": "hint", "hint": "s6_05_hint"},
            {"action": "accept-best"},
        ):
            assert (
                await client.post(f"/api/v1/projects/p-iso/runs/{rid_a}/controls", json=ctrl)
            ).status_code == 200
        a1_release.set()

        from opendv.models.policy import CEGAR_POLICY_SPECS
        from opendv.service.state_store import ServiceRunCheckpointStore

        base_b = CEGAR_POLICY_SPECS["balanced"].budget
        ckpt_store = ServiceRunCheckpointStore(svc.state_manager, "p-iso")
        await wait_run_via_sse(client, f"/api/v1/projects/p-iso/runs/{rid_a}/events")
        ckpt_a = ckpt_store.load_checkpoint(rid_a)
        assert ckpt_a is not None and ckpt_a.phase == "S6-05"
        assert ckpt_a.resume_payload.get("budget_max_turns") == base_b.max_turns + 6
        assert ckpt_a.resume_payload.get("budget_max_tokens") == base_b.max_tokens + 1200
        assert ckpt_a.resume_payload.get("budget_timeout_seconds") == base_b.timeout_seconds + 45
        assert ckpt_a.resume_payload.get("hints") == ["s6_05_hint"]
        assert ckpt_a.resume_payload.get("accept_best_requested") is True

        assert (
            await client.post(
                f"/api/v1/projects/p-iso/runs/{rid_a}/decisions",
                json={"answers": {"q-s6-05": "Yes"}},
            )
        ).status_code == 202
        await wait_run_via_sse(client, f"/api/v1/projects/p-iso/runs/{rid_a}/events")
        assert (await client.get(f"/api/v1/projects/p-iso/runs/{rid_a}")).json()["status"] == "COMPLETED"
        assert observed_ship[-1][1:] == (
            base_b.max_turns + 6,
            base_b.max_tokens + 1200,
            base_b.timeout_seconds + 45,
            ["s6_05_hint"],
        )
        assert observed_v8 == []  # accept_best_requested survived S6-05 and sealed without V8

        # 3) S6-06 Horizon hold preserves pre-consumed raise-budget and hints across decisions
        phase["mode"] = "s6_06"
        a1_started.clear()
        a1_release.clear()
        r_s6_b = await client.post("/api/v1/projects/p-iso/runs", json={"policy": "balanced"})
        assert r_s6_b.status_code == 202
        rid_b = r_s6_b.json()["run_id"]
        await asyncio.wait_for(a1_started.wait(), timeout=5.0)
        for ctrl in (
            {
                "action": "raise-budget",
                "extra_turns": 5,
                "extra_tokens": 1000,
                "extra_timeout_seconds": 30,
            },
            {"action": "hint", "hint": "s6_06_hint"},
        ):
            assert (
                await client.post(f"/api/v1/projects/p-iso/runs/{rid_b}/controls", json=ctrl)
            ).status_code == 200
        a1_release.set()

        await wait_run_via_sse(client, f"/api/v1/projects/p-iso/runs/{rid_b}/events")
        ckpt_b1 = ckpt_store.load_checkpoint(rid_b)
        assert ckpt_b1 is not None and ckpt_b1.phase == "S6-06"
        assert ckpt_b1.resume_payload.get("budget_max_turns") == base_b.max_turns + 5
        assert ckpt_b1.resume_payload.get("budget_max_tokens") == base_b.max_tokens + 1000
        assert ckpt_b1.resume_payload.get("budget_timeout_seconds") == base_b.timeout_seconds + 30
        assert ckpt_b1.resume_payload.get("hints") == ["s6_06_hint"]

        # First resume hits second S6-06 hold -> V8 still sees raised budget and second ckpt keeps hints/budget
        assert (
            await client.post(
                f"/api/v1/projects/p-iso/runs/{rid_b}/decisions",
                json={"answers": {"hq-v8-low-score": "steer"}},
            )
        ).status_code == 202
        await wait_run_via_sse(client, f"/api/v1/projects/p-iso/runs/{rid_b}/events")
        ckpt_b2 = ckpt_store.load_checkpoint(rid_b)
        assert ckpt_b2 is not None and ckpt_b2.phase == "S6-06"
        assert ckpt_b2.resume_payload.get("budget_max_turns") == base_b.max_turns + 5
        assert ckpt_b2.resume_payload.get("hints") == ["s6_06_hint"]
        expected_v8_budget = (
            base_b.max_turns + 5,
            base_b.max_tokens + 1000,
            base_b.timeout_seconds + 30,
        )
        assert observed_v8 == [expected_v8_budget, expected_v8_budget]

        # Final resume completes with V8 seeing raised budget
        assert (
            await client.post(
                f"/api/v1/projects/p-iso/runs/{rid_b}/decisions",
                json={"answers": {"hq-v8-low-score": "steer"}},
            )
        ).status_code == 202
        await wait_run_via_sse(client, f"/api/v1/projects/p-iso/runs/{rid_b}/events")
        assert (await client.get(f"/api/v1/projects/p-iso/runs/{rid_b}")).json()["status"] == "COMPLETED"
        assert observed_v8[-1] == expected_v8_budget

        # Also verify S6-06 save_checkpoint preserves accept_best_requested when set
        from opendv.modeling._engine_helpers import save_checkpoint

        v8_p = save_checkpoint(
            run_id="run-s606-check",
            phase="S6-06",
            status="HOLD_CHECKPOINTED",
            ontology_version="1.0.0",
            src_fingerprint="fp",
            pending_questions=[],
            turns_used=1,
            tokens_used=100,
            token_count_source="estimated",
            seq_ref=[0],
            event_sink=None,
            checkpoint_store=None,
            extra={"v8_low_score_pause": True},
            last_sig_seq=2,
            hints=["s6_06_hint"],
            accept_best_requested=True,
            horizon_review_requested=False,
            effective_budget=base_b.model_copy(update={"max_turns": base_b.max_turns + 5}),
            policy="balanced",
        ).resume_payload
        assert v8_p["accept_best_requested"] is True
        assert v8_p["hints"] == ["s6_06_hint"]
        assert v8_p["budget_max_turns"] == base_b.max_turns + 5
        assert v8_p["last_sig_seq"] == 2


@pytest.mark.asyncio
async def test_r4_active_run_resume_does_not_clobber_lease_and_horizon_no_rev_leak(
    tmp_path: Path,
) -> None:
    from opendv.api._tasks import RunTaskManager
    from opendv.models.wire_dtos_discovery import OntologyCommitReq
    from opendv.service.workflows._run_adapters import acquire_project_job_lease, record_run_state

    copy_public_am_wave1(tmp_path / "ws")
    s4_started = asyncio.Event()
    s4_release = asyncio.Event()
    s6_started = asyncio.Event()
    s6_release = asyncio.Event()
    mode: dict[str, str] = {"s6": "hold_running"}

    class _BarrierHarness(DeterministicMockHarness):
        async def run_turn(
            self,
            request: Any,
            *,
            event_sink: Any = None,
            scout_dispatcher: Any = None,
        ) -> Any:
            if request.agent_id == "ontology-first-mate" and request.checkpoint is None:
                s4_started.set()
                await s4_release.wait()
            elif request.agent_id == "ship-vault":
                if mode["s6"] == "fail":
                    await asyncio.sleep(0.01)
                    raise RuntimeError("boom_for_failed_horizon_check")
                if not s6_started.is_set():
                    s6_started.set()
                    await s6_release.wait()
            return await super().run_turn(
                request, event_sink=event_sink, scout_dispatcher=scout_dispatcher
            )

    def _sel(req: Any) -> MockTurnScript:
        if req.agent_id == "ontology-first-mate":
            return MockTurnScript(
                structured_output={
                    "proposed_ops": [],
                    "clarification_questions": [],
                    "summary": "Step 4 done",
                }
            )
        if req.agent_id == "ship-vault":
            profs = load_project_profiles(svc, "p-r4-lease")
            return MockTurnScript(
                structured_output=build_wave1_am_draft("p-r4-lease", "0.1.0", profs)
            )
        if req.agent_id == "dv-semantic-critic":
            return MockTurnScript(
                structured_output=SemanticCriticVerdict(
                    score=95,
                    accepted_waiver_ids=["WAIV-AP004", "WAIV-AP004-NAV"],
                )
            )
        if req.agent_id == "dv-horizon-critic":
            return MockTurnScript(
                structured_output=HorizonCriticVerdict(score=93, classification="REASONABLY_STABLE")
            )
        return MockTurnScript(text_output="{}")

    svc = _make_svc(tmp_path, harness=_BarrierHarness(selector=_sel))
    svc.init_project(ProjectInitReq(project_id="p-r4-lease", domain_namespace="asset_mgmt"))
    src = svc.attach_source(
        "p-r4-lease",
        SourceAttachReq(kind="sqlite", uri_or_filename="public_am/am_sec_treasury_ibor.sqlite"),
    )
    pq = svc.attach_source(
        "p-r4-lease",
        SourceAttachReq(kind="parquet", uri_or_filename="public_am/am_esg_stewardship_proxy.parquet"),
    )
    md = svc.attach_source(
        "p-r4-lease",
        SourceAttachReq(kind="markdown", uri_or_filename="public_am/am_stewardship_domain_notes.md"),
    )
    app = create_app(svc)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        # 1) Step 4 answers while RUNNING -> 400, run stays RUNNING + lease held
        r4 = await client.post("/api/v1/projects/p-r4-lease/ontology/construct", json={})
        assert r4.status_code == 202
        rid4 = r4.json()["run_id"]
        await asyncio.wait_for(s4_started.wait(), timeout=5.0)
        assert svc.get_project("p-r4-lease").has_active_lease is True

        _assert_problem(
            await client.post(
                f"/api/v1/projects/p-r4-lease/ontology/runs/{rid4}/answers",
                json={"answers": {"q": "yes"}},
            ),
            status=400,
            error_code="SYNTAX_INVALID_ARGUMENT",
        )
        assert (
            await client.get(f"/api/v1/projects/p-r4-lease/ontology/runs/{rid4}")
        ).json()["status"] == "RUNNING"
        assert svc.get_project("p-r4-lease").has_active_lease is True
        _assert_problem(
            await client.post("/api/v1/projects/p-r4-lease/ontology/construct", json={}),
            status=409,
            error_code="PROJECT_JOB_ACTIVE",
        )
        s4_release.set()
        await wait_run_via_sse(client, f"/api/v1/projects/p-r4-lease/ontology/runs/{rid4}/events")
        assert (
            await client.get(f"/api/v1/projects/p-r4-lease/ontology/runs/{rid4}")
        ).json()["status"] == "COMPLETED"
        assert svc.get_project("p-r4-lease").has_active_lease is False

        # Commit W1 ontology for Step 6
        proj = svc.get_project("p-r4-lease")
        assert proj.active_ontology_rev_id is not None
        md_q = (
            svc.get_document_pages("p-r4-lease", md.source_id, page_start=1, page_end=1)[0]
            .masked_text.splitlines()[0]
            .strip()
        )
        svc.commit_ontology(
            "p-r4-lease",
            OntologyCommitReq(
                operations=build_wave1_commit_ops(
                    src.source_id, pq.source_id, md.source_id, md_q
                ),  # type: ignore[arg-type]
                base_revision_id=proj.active_ontology_rev_id,
                rationale="Commit W1 ontology for R4 lease/clobber regression test.",
            ),
        )

        # 2) Step 6 decisions & horizon while RUNNING and HOLD_REQUESTED -> 400, no clobber, no rev leak
        r6 = await client.post("/api/v1/projects/p-r4-lease/runs", json={"policy": "balanced"})
        assert r6.status_code == 202
        rid6 = r6.json()["run_id"]
        await asyncio.wait_for(s6_started.wait(), timeout=5.0)
        assert svc.get_project("p-r4-lease").has_active_lease is True
        hor_before = len(
            svc.state_manager.list_revisions("p-r4-lease", rev_kind="horizon_assumption")
        )

        _assert_problem(
            await client.post(
                f"/api/v1/projects/p-r4-lease/runs/{rid6}/decisions",
                json={"answers": {}},
            ),
            status=400,
            error_code="SYNTAX_INVALID_ARGUMENT",
        )
        _assert_problem(
            await client.post(
                f"/api/v1/projects/p-r4-lease/runs/{rid6}/horizon",
                json={"action": "accept", "notes": "while running"},
            ),
            status=400,
            error_code="SYNTAX_INVALID_ARGUMENT",
        )
        assert (await client.get(f"/api/v1/projects/p-r4-lease/runs/{rid6}")).json()["status"] == "RUNNING"
        assert svc.get_project("p-r4-lease").has_active_lease is True
        assert (
            len(svc.state_manager.list_revisions("p-r4-lease", rev_kind="horizon_assumption"))
            == hor_before
        )
        _assert_problem(
            await client.post("/api/v1/projects/p-r4-lease/runs", json={"policy": "fast"}),
            status=409,
            error_code="PROJECT_JOB_ACTIVE",
        )

        # Also verify HOLD_REQUESTED state rejects decisions/horizon without dropping lease
        r_pause = await client.post(
            f"/api/v1/projects/p-r4-lease/runs/{rid6}/controls",
            json={"action": "pause"},
        )
        assert r_pause.status_code == 200 and r_pause.json()["status"] == "HOLD_REQUESTED"
        _assert_problem(
            await client.post(
                f"/api/v1/projects/p-r4-lease/runs/{rid6}/decisions",
                json={"answers": {}},
            ),
            status=400,
            error_code="SYNTAX_INVALID_ARGUMENT",
        )
        assert svc.get_project("p-r4-lease").has_active_lease is True
        s6_release.set()
        await wait_run_via_sse(client, f"/api/v1/projects/p-r4-lease/runs/{rid6}/events")
        assert (await client.get(f"/api/v1/projects/p-r4-lease/runs/{rid6}")).json()["status"] == "COMPLETED"
        assert svc.get_project("p-r4-lease").has_active_lease is False

        # 3) Step 6 FAILED run rejects POST .../horizon with 400 and zero horizon_assumption leak
        mode["s6"] = "fail"
        r_fail = await client.post("/api/v1/projects/p-r4-lease/runs", json={"policy": "fast"})
        assert r_fail.status_code == 202
        rid_fail = r_fail.json()["run_id"]
        await wait_run_via_sse(client, f"/api/v1/projects/p-r4-lease/runs/{rid_fail}/events")
        assert (await client.get(f"/api/v1/projects/p-r4-lease/runs/{rid_fail}")).json()["status"] == "FAILED"
        hor_before_fail = len(
            svc.state_manager.list_revisions("p-r4-lease", rev_kind="horizon_assumption")
        )
        _assert_problem(
            await client.post(
                f"/api/v1/projects/p-r4-lease/runs/{rid_fail}/horizon",
                json={"action": "accept", "notes": "on failed run"},
            ),
            status=400,
            error_code="SYNTAX_INVALID_ARGUMENT",
        )
        assert (
            len(svc.state_manager.list_revisions("p-r4-lease", rev_kind="horizon_assumption"))
            == hor_before_fail
        )

    # 4) Legitimate zero-await OpenDVError on fresh RunTaskManager.launch_run still marks FAILED & releases lease
    tm = RunTaskManager()
    fresh_rid = "run6-zeroawait1"

    async def _zero_await_boom(rid: str) -> Any:
        acquire_project_job_lease(svc.state_manager, "p-r4-lease", rid)
        from opendv.models.wire_dtos_discovery import RunStateResp

        record_run_state(
            svc.state_manager,
            "p-r4-lease",
            RunStateResp(
                run_id=rid,
                project_id="p-r4-lease",
                step="step6",
                status="RUNNING",
                policy="fast",
                phase="cegar_synthesis",
                attempt=0,
                budget_turns_used=0,
                budget_tokens_used=0,
            ),
        )
        raise OpenDVError(
            "Zero-await provider error",
            error_code=ErrorCode.AGENT_NOT_CONFIGURED,
            run_id=rid,
        )

    with pytest.raises(OpenDVError):
        await tm.launch_run(svc, "p-r4-lease", fresh_rid, _zero_await_boom, step="step6")
    assert svc.get_run("p-r4-lease", fresh_rid).status == "FAILED"
    assert svc.get_project("p-r4-lease").has_active_lease is False


@pytest.mark.asyncio
async def test_r4_certified_fast_in_place_horizon_upgrade_wave1_and_wave2(
    tmp_path: Path,
) -> None:
    from opendv.models.wire_dtos_discovery import OntologyCommitReq

    copy_public_am_wave1(tmp_path / "ws")
    state: dict[str, Any] = {"wave": 1, "v8_score": 93}

    def _sel(req: Any) -> MockTurnScript:
        pid = str(json.loads(req.prompt)["project_id"]) if req.agent_id == "ship-vault" else "p-fast-1"
        if req.agent_id == "ship-vault":
            profs = load_project_profiles(svc, pid)
            if state["wave"] == 1:
                return MockTurnScript(structured_output=build_wave1_am_draft(pid, "0.1.0", profs))
            return MockTurnScript(structured_output=build_wave2_am_draft(pid, "0.2.0", profs))
        if req.agent_id == "dv-semantic-critic":
            return MockTurnScript(
                structured_output=SemanticCriticVerdict(
                    score=95,
                    accepted_waiver_ids=["WAIV-AP004", "WAIV-AP004-NAV"],
                )
            )
        if req.agent_id == "dv-horizon-critic":
            return MockTurnScript(
                structured_output=HorizonCriticVerdict(
                    score=int(state["v8_score"]),
                    classification=(
                        "REASONABLY_STABLE"
                        if int(state["v8_score"]) >= 85
                        else "HUMAN_REVIEW_REQUIRED"
                    ),
                )
            )
        return MockTurnScript(text_output="{}")

    svc = _make_svc(tmp_path, harness=DeterministicMockHarness(selector=_sel))
    for pid in ("p-fast-1", "p-fast-2"):
        svc.init_project(ProjectInitReq(project_id=pid, domain_namespace="asset_mgmt"))
        s_sql = svc.attach_source(
            pid,
            SourceAttachReq(kind="sqlite", uri_or_filename="public_am/am_sec_treasury_ibor.sqlite"),
        )
        s_pq = svc.attach_source(
            pid,
            SourceAttachReq(
                kind="parquet", uri_or_filename="public_am/am_esg_stewardship_proxy.parquet"
            ),
        )
        s_md = svc.attach_source(
            pid,
            SourceAttachReq(
                kind="markdown", uri_or_filename="public_am/am_stewardship_domain_notes.md"
            ),
        )
        proj = svc.get_project(pid)
        assert proj.active_ontology_rev_id is not None
        md_q = (
            svc.get_document_pages(pid, s_md.source_id, page_start=1, page_end=1)[0]
            .masked_text.splitlines()[0]
            .strip()
        )
        svc.commit_ontology(
            pid,
            OntologyCommitReq(
                operations=build_wave1_commit_ops(
                    s_sql.source_id, s_pq.source_id, s_md.source_id, md_q
                ),  # type: ignore[arg-type]
                base_revision_id=proj.active_ontology_rev_id,
                rationale="Commit W1 ontology for fast horizon upgrade tests.",
            ),
        )

    app = create_app(svc)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        # 1) Wave 1 CERTIFIED_FAST -> POST .../controls {"action": "horizon-review"} (direct pass)
        r_f1 = await client.post("/api/v1/projects/p-fast-1/runs", json={"policy": "fast"})
        assert r_f1.status_code == 202
        rid_f1 = r_f1.json()["run_id"]
        await wait_run_via_sse(client, f"/api/v1/projects/p-fast-1/runs/{rid_f1}/events")
        st_f1 = (await client.get(f"/api/v1/projects/p-fast-1/runs/{rid_f1}")).json()
        assert st_f1["status"] == "COMPLETED" and st_f1["phase"] == "wave_certified_fast"
        old_wave_id_1 = svc.get_project("p-fast-1").active_wave_id

        r_up1 = await client.post(
            f"/api/v1/projects/p-fast-1/runs/{rid_f1}/controls",
            json={"action": "horizon-review"},
        )
        assert r_up1.status_code == 200
        up1 = r_up1.json()
        assert up1["status"] == "COMPLETED" and up1["phase"] == "wave_certified"
        new_wave_id_1 = svc.get_project("p-fast-1").active_wave_id
        assert new_wave_id_1 is not None and new_wave_id_1 != old_wave_id_1
        ir1 = (await client.get("/api/v1/projects/p-fast-1/vault/ir")).json()
        assert ir1["wave_number"] == 1
        assert svc.get_project("p-fast-1").has_active_lease is False

        # 2) Wave 1 CERTIFIED_FAST -> POST .../horizon {"action": "accept"} (direct pass)
        r_f2 = await client.post("/api/v1/projects/p-fast-2/runs", json={"policy": "fast"})
        assert r_f2.status_code == 202
        rid_f2 = r_f2.json()["run_id"]
        await wait_run_via_sse(client, f"/api/v1/projects/p-fast-2/runs/{rid_f2}/events")
        old_wave_id_2 = svc.get_project("p-fast-2").active_wave_id

        r_hor2 = await client.post(
            f"/api/v1/projects/p-fast-2/runs/{rid_f2}/horizon",
            json={"action": "accept", "notes": "Upgrade Wave 1 fast via POST /horizon"},
        )
        assert r_hor2.status_code == 202
        await wait_run_via_sse(client, f"/api/v1/projects/p-fast-2/runs/{rid_f2}/events")
        st_hor2 = (await client.get(f"/api/v1/projects/p-fast-2/runs/{rid_f2}")).json()
        assert st_hor2["status"] == "COMPLETED" and st_hor2["phase"] == "wave_certified"
        assert svc.get_project("p-fast-2").active_wave_id != old_wave_id_2
        assert (await client.get("/api/v1/projects/p-fast-2/vault/ir")).json()["wave_number"] == 1

        # 3) Wave 1 CERTIFIED_FAST -> horizon-review -> S6-06 HOLD -> POST .../decisions (in-place Wave 1)
        state["v8_score"] = 71
        await client.post(
            "/api/v1/projects",
            json={"project_id": "p-fast-3", "domain_namespace": "asset_mgmt"},
        )
        s3_sql = (
            await client.post(
                "/api/v1/projects/p-fast-3/sources",
                json={"kind": "sqlite", "uri_or_filename": "public_am/am_sec_treasury_ibor.sqlite"},
            )
        ).json()
        s3_pq = (
            await client.post(
                "/api/v1/projects/p-fast-3/sources",
                json={"kind": "parquet", "uri_or_filename": "public_am/am_esg_stewardship_proxy.parquet"},
            )
        ).json()
        s3_md = (
            await client.post(
                "/api/v1/projects/p-fast-3/sources",
                json={"kind": "markdown", "uri_or_filename": "public_am/am_stewardship_domain_notes.md"},
            )
        ).json()
        p3 = svc.get_project("p-fast-3")
        assert p3.active_ontology_rev_id is not None
        md_q3 = (
            svc.get_document_pages("p-fast-3", s3_md["source_id"], page_start=1, page_end=1)[0]
            .masked_text.splitlines()[0]
            .strip()
        )
        svc.commit_ontology(
            "p-fast-3",
            OntologyCommitReq(
                operations=build_wave1_commit_ops(
                    s3_sql["source_id"], s3_pq["source_id"], s3_md["source_id"], md_q3
                ),  # type: ignore[arg-type]
                base_revision_id=p3.active_ontology_rev_id,
                rationale="Commit W1 ontology for S6-06 fast hold resume test.",
            ),
        )
        r_w1_hold = await client.post("/api/v1/projects/p-fast-3/runs", json={"policy": "fast"})
        assert r_w1_hold.status_code == 202
        rid_w1h = r_w1_hold.json()["run_id"]
        await wait_run_via_sse(client, f"/api/v1/projects/p-fast-3/runs/{rid_w1h}/events")
        r_ctrl_w1h = await client.post(
            f"/api/v1/projects/p-fast-3/runs/{rid_w1h}/controls",
            json={"action": "horizon-review"},
        )
        assert r_ctrl_w1h.status_code == 200
        assert r_ctrl_w1h.json()["status"] == "HOLD_CHECKPOINTED"

        r_dec_w1h = await client.post(
            f"/api/v1/projects/p-fast-3/runs/{rid_w1h}/decisions",
            json={"answers": {"hq-v8-low-score": "accept"}},
        )
        assert r_dec_w1h.status_code == 202
        await wait_run_via_sse(client, f"/api/v1/projects/p-fast-3/runs/{rid_w1h}/events")
        st_w1h = (await client.get(f"/api/v1/projects/p-fast-3/runs/{rid_w1h}")).json()
        assert st_w1h["status"] == "COMPLETED" and st_w1h["phase"] == "wave_certified"
        assert (await client.get("/api/v1/projects/p-fast-3/vault/ir")).json()["wave_number"] == 1
        w1_revs = svc.state_manager.list_revisions("p-fast-3", rev_kind="wave_ir")
        assert all(r["rev_key"].startswith(f"wave-1-{rid_w1h}") for r in w1_revs)

        # 4) Wave 2 CERTIFIED_FAST -> horizon-review -> S6-06 HOLD -> POST .../decisions AND POST .../horizon
        copy_public_am_wave2(tmp_path / "ws")
        for w2_kind, w2_file in (
            ("parquet", "public_am/wave2_eu_custody_lei_and_drift.parquet"),
            ("csv", "public_am/wave2_bitemporal_nav_restatements.csv"),
            ("csv", "public_am/wave2_ambiguous_otc_collateral.csv"),
            ("csv", "public_am/prime_allocations_w2.csv"),
        ):
            assert (
                await client.post(
                    "/api/v1/projects/p-fast-1/sources",
                    json={"kind": w2_kind, "uri_or_filename": w2_file},
                )
            ).status_code == 200

        w2_srcs = (await client.get("/api/v1/projects/p-fast-1/sources")).json()
        w2_eu = next(s for s in w2_srcs if "wave2_eu" in s["uri_or_filename"])
        w2_nav = next(s for s in w2_srcs if "bitemporal_nav" in s["uri_or_filename"])
        w2_otc = next(s for s in w2_srcs if "otc_collateral" in s["uri_or_filename"])
        w2_alloc = next(s for s in w2_srcs if "prime_allocations" in s["uri_or_filename"])
        p1_now = svc.get_project("p-fast-1")
        assert p1_now.active_ontology_rev_id is not None
        svc.commit_ontology(
            "p-fast-1",
            OntologyCommitReq(
                operations=[
                    AddConcept(
                        concept_uri="urn:asset_mgmt:CustodyAccount",
                        concept_name="CustodyAccount",
                        description="EU custody account",
                        key_authority_tier="TIER_2_ENTERPRISE_SOR",
                        table_bindings=[
                            ConceptTableBinding(
                                source_id=w2_eu["source_id"],
                                table_id=f"{w2_eu['source_id']}:wave2_eu_custody_lei_and_drift",
                                col_name="lei_code",
                            )
                        ],
                    ),
                    AddConcept(
                        concept_uri="urn:asset_mgmt:SecurityIsin",
                        concept_name="Security Isin",
                        description="Global ISIN",
                        key_authority_tier="TIER_1_GLOBAL_STANDARD",
                        table_bindings=[
                            ConceptTableBinding(
                                source_id=w2_eu["source_id"],
                                table_id=f"{w2_eu['source_id']}:wave2_eu_custody_lei_and_drift",
                                col_name="isin",
                            )
                        ],
                    ),
                    AddConcept(
                        concept_uri="urn:asset_mgmt:FundNav",
                        concept_name="Fund Nav",
                        description="Fund NAV",
                        key_authority_tier="TIER_2_ENTERPRISE_SOR",
                        table_bindings=[
                            ConceptTableBinding(
                                source_id=w2_nav["source_id"],
                                table_id=f"{w2_nav['source_id']}:wave2_bitemporal_nav_restatements",
                                col_name="fund_code",
                            )
                        ],
                    ),
                    AddConcept(
                        concept_uri="urn:asset_mgmt:OtcCollateral",
                        concept_name="Otc Collateral",
                        description="OTC collateral",
                        key_authority_tier="TIER_1_GLOBAL_STANDARD",
                        table_bindings=[
                            ConceptTableBinding(
                                source_id=w2_otc["source_id"],
                                table_id=f"{w2_otc['source_id']}:wave2_ambiguous_otc_collateral",
                                col_name="uti_id",
                            )
                        ],
                    ),
                    AddConcept(
                        concept_uri="urn:asset_mgmt:PrimeAllocation",
                        concept_name="Prime Allocation",
                        description="Prime allocation",
                        key_authority_tier="TIER_2_ENTERPRISE_SOR",
                        table_bindings=[
                            ConceptTableBinding(
                                source_id=w2_alloc["source_id"],
                                table_id=f"{w2_alloc['source_id']}:prime_allocations_w2",
                                col_name="block_id",
                            )
                        ],
                    ),
                ],
                base_revision_id=p1_now.active_ontology_rev_id,
                rationale="Commit W2 ontology for Wave 2 fast upgrade tests.",
            ),
        )

        assert (
            await client.patch(
                "/api/v1/projects/p-fast-1/settings",
                json={"real_row_proving": False},
            )
        ).status_code == 200
        state["wave"] = 2
        state["v8_score"] = 71
        r_w2 = await client.post("/api/v1/projects/p-fast-1/runs", json={"policy": "fast"})
        assert r_w2.status_code == 202
        rid_w2 = r_w2.json()["run_id"]
        await wait_run_via_sse(client, f"/api/v1/projects/p-fast-1/runs/{rid_w2}/events")
        assert (await client.get(f"/api/v1/projects/p-fast-1/runs/{rid_w2}")).json()["phase"] == "wave_certified_fast"

        r_ctrl_w2 = await client.post(
            f"/api/v1/projects/p-fast-1/runs/{rid_w2}/controls",
            json={"action": "horizon-review"},
        )
        assert r_ctrl_w2.status_code == 200
        assert r_ctrl_w2.json()["status"] == "HOLD_CHECKPOINTED"

        r_dec_w2 = await client.post(
            f"/api/v1/projects/p-fast-1/runs/{rid_w2}/decisions",
            json={"answers": {"hq-v8-low-score": "accept"}},
        )
        assert r_dec_w2.status_code == 202
        await wait_run_via_sse(client, f"/api/v1/projects/p-fast-1/runs/{rid_w2}/events")
        st_w2 = (await client.get(f"/api/v1/projects/p-fast-1/runs/{rid_w2}")).json()
        assert st_w2["status"] == "COMPLETED" and st_w2["phase"] == "wave_certified"
        ir_w2 = (await client.get("/api/v1/projects/p-fast-1/vault/ir")).json()
        assert ir_w2["wave_number"] == 2
        assert ir_w2["wave_delta"]["from_wave"] == 1
        assert ir_w2["wave_delta"]["to_wave"] == 2

        # Also verify POST .../horizon on COMPLETED Wave 2 upgraded run works cleanly
        state["v8_score"] = 94
        r_hor_w2 = await client.post(
            f"/api/v1/projects/p-fast-1/runs/{rid_w2}/horizon",
            json={"action": "accept", "notes": "Re-verify Wave 2 horizon"},
        )
        assert r_hor_w2.status_code == 202
        await wait_run_via_sse(client, f"/api/v1/projects/p-fast-1/runs/{rid_w2}/events")
        st_w2_re = (await client.get(f"/api/v1/projects/p-fast-1/runs/{rid_w2}")).json()
        assert st_w2_re["status"] == "COMPLETED" and st_w2_re["phase"] == "wave_certified"
        assert svc.get_project("p-fast-1").has_active_lease is False


@pytest.mark.asyncio
async def test_r5_post_waiver_wave2_horizon_preserves_locks_and_isolates_low_score(
    tmp_path: Path,
) -> None:
    from opendv.models.wire_dtos_discovery import OntologyCommitReq
    from opendv.verifier._v5_evolution import verify_v5_evolution

    copy_public_am_wave1(tmp_path / "ws")
    copy_public_am_wave2(tmp_path / "ws")
    state: dict[str, Any] = {"wave": 1, "v8_score": 71}
    v8_prompts: list[dict[str, Any]] = []

    def _sel(req: Any) -> MockTurnScript:
        if req.agent_id == "ship-vault":
            profs = load_project_profiles(svc, "p-r5-waiv")
            if state["wave"] == 1:
                return MockTurnScript(
                    structured_output=build_wave1_am_draft("p-r5-waiv", "0.1.0", profs)
                )
            return MockTurnScript(
                structured_output=build_wave2_am_draft("p-r5-waiv", "0.2.0", profs)
            )
        if req.agent_id == "dv-semantic-critic":
            return MockTurnScript(
                structured_output=SemanticCriticVerdict(
                    score=95,
                    accepted_waiver_ids=["WAIV-AP004", "WAIV-AP004-NAV"],
                )
            )
        if req.agent_id == "dv-horizon-critic":
            v8_prompts.append(json.loads(req.prompt))
            return MockTurnScript(
                structured_output=HorizonCriticVerdict(
                    score=int(state["v8_score"]),
                    classification=(
                        "REASONABLY_STABLE"
                        if int(state["v8_score"]) >= 85
                        else "HUMAN_REVIEW_REQUIRED"
                    ),
                )
            )
        return MockTurnScript(text_output="{}")

    mock_harness = DeterministicMockHarness(selector=_sel)
    svc = _make_svc(tmp_path, harness=mock_harness)
    svc.init_project(ProjectInitReq(project_id="p-r5-waiv", domain_namespace="asset_mgmt"))
    s_sql = svc.attach_source(
        "p-r5-waiv",
        SourceAttachReq(kind="sqlite", uri_or_filename="public_am/am_sec_treasury_ibor.sqlite"),
    )
    s_pq = svc.attach_source(
        "p-r5-waiv",
        SourceAttachReq(kind="parquet", uri_or_filename="public_am/am_esg_stewardship_proxy.parquet"),
    )
    s_md = svc.attach_source(
        "p-r5-waiv",
        SourceAttachReq(kind="markdown", uri_or_filename="public_am/am_stewardship_domain_notes.md"),
    )
    proj = svc.get_project("p-r5-waiv")
    assert proj.active_ontology_rev_id is not None
    md_q = (
        svc.get_document_pages("p-r5-waiv", s_md.source_id, page_start=1, page_end=1)[0]
        .masked_text.splitlines()[0]
        .strip()
    )
    svc.commit_ontology(
        "p-r5-waiv",
        OntologyCommitReq(
            operations=build_wave1_commit_ops(
                s_sql.source_id, s_pq.source_id, s_md.source_id, md_q
            ),  # type: ignore[arg-type]
            base_revision_id=proj.active_ontology_rev_id,
            rationale="Commit W1 ontology for R5 waiver + low-score isolation E2E.",
        ),
    )

    app = create_app(svc)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        # 1) Wave 1 fast -> horizon-review (V8=71 -> S6-06 HOLD) -> decisions(accept) -> CERTIFIED
        r_w1 = await client.post("/api/v1/projects/p-r5-waiv/runs", json={"policy": "fast"})
        assert r_w1.status_code == 202
        rid_w1 = r_w1.json()["run_id"]
        await wait_run_via_sse(client, f"/api/v1/projects/p-r5-waiv/runs/{rid_w1}/events")

        r_w1_ctrl = await client.post(
            f"/api/v1/projects/p-r5-waiv/runs/{rid_w1}/controls",
            json={"action": "horizon-review"},
        )
        assert r_w1_ctrl.status_code == 200
        assert r_w1_ctrl.json()["status"] == "HOLD_CHECKPOINTED"

        r_w1_dec = await client.post(
            f"/api/v1/projects/p-r5-waiv/runs/{rid_w1}/decisions",
            json={"answers": {"hq-v8-low-score": "accept"}},
        )
        assert r_w1_dec.status_code == 202
        await wait_run_via_sse(client, f"/api/v1/projects/p-r5-waiv/runs/{rid_w1}/events")
        assert (await client.get(f"/api/v1/projects/p-r5-waiv/runs/{rid_w1}")).json()[
            "phase"
        ] == "wave_certified"

        # 2) Submit Steward waiver on Wave 1 (creates wave-1-waived-<hex6> without child lock)
        r_waiv = await client.post(
            "/api/v1/projects/p-r5-waiv/vault/waivers",
            json={
                "waiver_id": "WAIV-R5-001",
                "rule_id": "DV-AP-004",
                "entity_name": "hub_filer",
                "rationale": "Steward waiver for institutional filer hub in Wave 1",
            },
        )
        assert r_waiv.status_code == 200
        waived_wave_id = svc.get_project("p-r5-waiv").active_wave_id
        assert waived_wave_id is not None and waived_wave_id.startswith("wave-1-waived-")

        # 3) Attach Wave 2 sources, commit W2 ontology, and run Wave 2 fast -> COMPLETED
        for w2_kind, w2_file in (
            ("parquet", "public_am/wave2_eu_custody_lei_and_drift.parquet"),
            ("csv", "public_am/wave2_bitemporal_nav_restatements.csv"),
            ("csv", "public_am/wave2_ambiguous_otc_collateral.csv"),
            ("csv", "public_am/prime_allocations_w2.csv"),
        ):
            assert (
                await client.post(
                    "/api/v1/projects/p-r5-waiv/sources",
                    json={"kind": w2_kind, "uri_or_filename": w2_file},
                )
            ).status_code == 200

        w2_srcs = (await client.get("/api/v1/projects/p-r5-waiv/sources")).json()
        p_waiv_now = svc.get_project("p-r5-waiv")
        assert p_waiv_now.active_ontology_rev_id is not None
        svc.commit_ontology(
            "p-r5-waiv",
            OntologyCommitReq(
                operations=build_wave2_commit_ops(w2_srcs),  # type: ignore[arg-type]
                base_revision_id=p_waiv_now.active_ontology_rev_id,
                rationale="Commit W2 ontology for R5 post-waiver lock test.",
            ),
        )

        assert (
            await client.patch(
                "/api/v1/projects/p-r5-waiv/settings",
                json={"real_row_proving": False},
            )
        ).status_code == 200
        state["wave"] = 2
        r_w2 = await client.post("/api/v1/projects/p-r5-waiv/runs", json={"policy": "fast"})
        assert r_w2.status_code == 202
        rid_w2 = r_w2.json()["run_id"]
        await wait_run_via_sse(client, f"/api/v1/projects/p-r5-waiv/runs/{rid_w2}/events")
        assert (await client.get(f"/api/v1/projects/p-r5-waiv/runs/{rid_w2}")).json()[
            "phase"
        ] == "wave_certified_fast"

        # 4) Wave 2 horizon-review with V8=71 MUST pause at S6-06 (Finding 2) AND have non-null Wave 1 locks (Finding 1)
        v8_prompts.clear()
        r_w2_ctrl = await client.post(
            f"/api/v1/projects/p-r5-waiv/runs/{rid_w2}/controls",
            json={"action": "horizon-review"},
        )
        assert r_w2_ctrl.status_code == 200
        w2_ctrl_body = r_w2_ctrl.json()
        assert w2_ctrl_body["status"] == "HOLD_CHECKPOINTED"
        assert w2_ctrl_body["pending_questions"][0]["question_id"] == "hq-v8-low-score"
        assert len(v8_prompts) == 1
        assert v8_prompts[-1]["evolution_locks"] is not None
        assert v8_prompts[-1]["evolution_locks"]["wave_number"] == 1
        assert "hub_filer" in v8_prompts[-1]["evolution_locks"]["locked_hub_bks"]

        # Verify prepare_resume_cegar_request preserves EvolutionLockSpec and enforces DV-EVO-*
        from opendv.service.workflows._vault_helpers import (
            load_cegar_result,
            prepare_resume_cegar_request,
        )

        w2_res_hold = load_cegar_result(svc.state_manager, "p-r5-waiv", rid_w2)
        assert w2_res_hold is not None
        _, _, resume_req = prepare_resume_cegar_request(
            svc.state_manager,
            svc.workspace_root,
            project_id="p-r5-waiv",
            run_id=rid_w2,
            policy="fast",
            harness=mock_harness,
            prior_res=w2_res_hold,
        )
        assert resume_req.wave.evolution_locks is not None
        assert resume_req.wave.evolution_locks.wave_number == 1
        assert w2_res_hold.certified_ir is not None
        mutated_ir = w2_res_hold.certified_ir.model_copy(
            update={"hubs": [h for h in w2_res_hold.certified_ir.hubs if h.hub_name != "hub_filer"]}
        )
        evo_diags = verify_v5_evolution(
            mutated_ir,
            locks=resume_req.wave.evolution_locks,
            prior_ir=resume_req.wave.prior_certified_ir,
        )
        assert any(d.rule_id == "DV-EVO-002" and d.entity_name == "hub_filer" for d in evo_diags)

        # Resume Wave 2 S6-06 hold -> CERTIFIED, with evolution_locks still non-null in V8 prompt
        r_w2_dec = await client.post(
            f"/api/v1/projects/p-r5-waiv/runs/{rid_w2}/decisions",
            json={"answers": {"hq-v8-low-score": "accept"}},
        )
        assert r_w2_dec.status_code == 202
        await wait_run_via_sse(client, f"/api/v1/projects/p-r5-waiv/runs/{rid_w2}/events")
        st_w2 = (await client.get(f"/api/v1/projects/p-r5-waiv/runs/{rid_w2}")).json()
        assert st_w2["status"] == "COMPLETED" and st_w2["phase"] == "wave_certified"
        assert v8_prompts[-1]["evolution_locks"] is not None
        assert v8_prompts[-1]["evolution_locks"]["wave_number"] == 1


@pytest.mark.asyncio
async def test_r5_horizon_assumptions_pause_watermark_and_out_of_order_active_wave(
    tmp_path: Path,
) -> None:
    from opendv.models.wire_dtos_discovery import OntologyCommitReq
    from opendv.service.workflows._vault_helpers import load_cegar_result, load_verifier_inputs

    copy_public_am_wave1(tmp_path / "ws")
    copy_public_am_wave2(tmp_path / "ws")
    state: dict[str, Any] = {"wave": 1, "v8_score": 71, "pause_gate": True}
    a1_started = asyncio.Event()
    a1_release = asyncio.Event()
    v8_prompts: list[dict[str, Any]] = []

    class _PauseBarrierHarness(DeterministicMockHarness):
        async def run_turn(
            self,
            request: Any,
            *,
            event_sink: Any = None,
            scout_dispatcher: Any = None,
        ) -> Any:
            if request.agent_id == "ship-vault" and state["pause_gate"]:
                state["pause_gate"] = False
                a1_started.set()
                await a1_release.wait()
            return await super().run_turn(
                request, event_sink=event_sink, scout_dispatcher=scout_dispatcher
            )

    def _sel(req: Any) -> MockTurnScript:
        if req.agent_id == "ship-vault":
            profs = load_project_profiles(svc, "p-r5-h345")
            if state["wave"] == 1:
                return MockTurnScript(
                    structured_output=build_wave1_am_draft("p-r5-h345", "0.1.0", profs)
                )
            return MockTurnScript(
                structured_output=build_wave2_am_draft("p-r5-h345", "0.2.0", profs)
            )
        if req.agent_id == "dv-semantic-critic":
            return MockTurnScript(
                structured_output=SemanticCriticVerdict(
                    score=95,
                    accepted_waiver_ids=["WAIV-AP004", "WAIV-AP004-NAV"],
                )
            )
        if req.agent_id == "dv-horizon-critic":
            v8_prompts.append(json.loads(req.prompt))
            return MockTurnScript(
                structured_output=HorizonCriticVerdict(
                    score=int(state["v8_score"]),
                    classification=(
                        "REASONABLY_STABLE"
                        if int(state["v8_score"]) >= 85
                        else "HUMAN_REVIEW_REQUIRED"
                    ),
                )
            )
        return MockTurnScript(text_output="{}")

    svc = _make_svc(tmp_path, harness=_PauseBarrierHarness(selector=_sel))
    svc.init_project(ProjectInitReq(project_id="p-r5-h345", domain_namespace="asset_mgmt"))
    s_sql = svc.attach_source(
        "p-r5-h345",
        SourceAttachReq(kind="sqlite", uri_or_filename="public_am/am_sec_treasury_ibor.sqlite"),
    )
    s_pq = svc.attach_source(
        "p-r5-h345",
        SourceAttachReq(kind="parquet", uri_or_filename="public_am/am_esg_stewardship_proxy.parquet"),
    )
    s_md = svc.attach_source(
        "p-r5-h345",
        SourceAttachReq(kind="markdown", uri_or_filename="public_am/am_stewardship_domain_notes.md"),
    )
    proj = svc.get_project("p-r5-h345")
    assert proj.active_ontology_rev_id is not None
    md_q = (
        svc.get_document_pages("p-r5-h345", s_md.source_id, page_start=1, page_end=1)[0]
        .masked_text.splitlines()[0]
        .strip()
    )
    svc.commit_ontology(
        "p-r5-h345",
        OntologyCommitReq(
            operations=build_wave1_commit_ops(
                s_sql.source_id, s_pq.source_id, s_md.source_id, md_q
            ),  # type: ignore[arg-type]
            base_revision_id=proj.active_ontology_rev_id,
            rationale="Commit W1 ontology for R5 horizon/pause/active_wave_id E2E.",
        ),
    )

    app = create_app(svc)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        # 1) Finding 4: pre-completion pause during policy="fast" -> COMPLETED -> horizon-review -> S6-06 HOLD -> decisions -> COMPLETED
        r_f = await client.post("/api/v1/projects/p-r5-h345/runs", json={"policy": "fast"})
        assert r_f.status_code == 202
        rid_w1 = r_f.json()["run_id"]
        await asyncio.wait_for(a1_started.wait(), timeout=5.0)
        r_p = await client.post(
            f"/api/v1/projects/p-r5-h345/runs/{rid_w1}/controls",
            json={"action": "pause"},
        )
        assert r_p.status_code == 200 and r_p.json()["status"] == "HOLD_REQUESTED"
        a1_release.set()
        await wait_run_via_sse(client, f"/api/v1/projects/p-r5-h345/runs/{rid_w1}/events")
        st_f = (await client.get(f"/api/v1/projects/p-r5-h345/runs/{rid_w1}")).json()
        assert st_f["status"] == "COMPLETED" and st_f["phase"] == "wave_certified_fast"

        r_hr = await client.post(
            f"/api/v1/projects/p-r5-h345/runs/{rid_w1}/controls",
            json={"action": "horizon-review"},
        )
        assert r_hr.status_code == 200 and r_hr.json()["status"] == "HOLD_CHECKPOINTED"

        r_dec = await client.post(
            f"/api/v1/projects/p-r5-h345/runs/{rid_w1}/decisions",
            json={"answers": {"hq-v8-low-score": "accept"}},
        )
        assert r_dec.status_code == 202
        await wait_run_via_sse(client, f"/api/v1/projects/p-r5-h345/runs/{rid_w1}/events")
        st_done = (await client.get(f"/api/v1/projects/p-r5-h345/runs/{rid_w1}")).json()
        assert st_done["status"] == "COMPLETED" and st_done["phase"] == "wave_certified"

        # 2) Finding 3: POST .../horizon on COMPLETED CERTIFIED_FAST AND COMPLETED CERTIFIED passes new assumption
        state["v8_score"] = 93
        r_fast_only = await client.post("/api/v1/projects/p-r5-h345/runs", json={"policy": "fast"})
        assert r_fast_only.status_code == 202
        rid_fast_only = r_fast_only.json()["run_id"]
        await wait_run_via_sse(client, f"/api/v1/projects/p-r5-h345/runs/{rid_fast_only}/events")
        assert (await client.get(f"/api/v1/projects/p-r5-h345/runs/{rid_fast_only}")).json()[
            "phase"
        ] == "wave_certified_fast"

        v8_prompts.clear()
        r_hor_fast = await client.post(
            f"/api/v1/projects/p-r5-h345/runs/{rid_fast_only}/horizon",
            json={
                "action": "accept",
                "notes": "Immediate fast critic note",
                "added_fields": ["fast_audit_col"],
            },
        )
        assert r_hor_fast.status_code == 202
        await wait_run_via_sse(client, f"/api/v1/projects/p-r5-h345/runs/{rid_fast_only}/events")
        assert len(v8_prompts) == 1
        assert any(
            a["notes"] == "Immediate fast critic note"
            and a["added_fields"] == ["fast_audit_col"]
            and a["steward_decision"] == "ACCEPT"
            for a in v8_prompts[0]["horizon_assumptions"]
        )
        fast_only_res = load_cegar_result(svc.state_manager, "p-r5-h345", rid_fast_only)
        assert fast_only_res is not None
        assert any(
            a.notes == "Immediate fast critic note" and a.added_fields == ["fast_audit_col"]
            for a in fast_only_res.horizon_assumptions
        )

        v8_prompts.clear()
        r_hor = await client.post(
            f"/api/v1/projects/p-r5-h345/runs/{rid_w1}/horizon",
            json={
                "action": "accept",
                "notes": "Immediate steward horizon guidance",
                "added_fields": ["audit_ts"],
            },
        )
        assert r_hor.status_code == 202
        await wait_run_via_sse(client, f"/api/v1/projects/p-r5-h345/runs/{rid_w1}/events")
        assert len(v8_prompts) == 1
        assert any(
            a["notes"] == "Immediate steward horizon guidance"
            and a["added_fields"] == ["audit_ts"]
            and a["steward_decision"] == "ACCEPT"
            for a in v8_prompts[0]["horizon_assumptions"]
        )
        w1_res = load_cegar_result(svc.state_manager, "p-r5-h345", rid_w1)
        assert w1_res is not None
        assert any(
            a.notes == "Immediate steward horizon guidance" and a.added_fields == ["audit_ts"]
            for a in w1_res.horizon_assumptions
        )

        # 3) Finding 5: Wave 2 completes, then out-of-order POST .../horizon on Wave 1 does NOT regress active_wave_id
        for w2_kind, w2_file in (
            ("parquet", "public_am/wave2_eu_custody_lei_and_drift.parquet"),
            ("csv", "public_am/wave2_bitemporal_nav_restatements.csv"),
            ("csv", "public_am/wave2_ambiguous_otc_collateral.csv"),
            ("csv", "public_am/prime_allocations_w2.csv"),
        ):
            assert (
                await client.post(
                    "/api/v1/projects/p-r5-h345/sources",
                    json={"kind": w2_kind, "uri_or_filename": w2_file},
                )
            ).status_code == 200

        w2_srcs_h = (await client.get("/api/v1/projects/p-r5-h345/sources")).json()
        p_h_now = svc.get_project("p-r5-h345")
        assert p_h_now.active_ontology_rev_id is not None
        svc.commit_ontology(
            "p-r5-h345",
            OntologyCommitReq(
                operations=build_wave2_commit_ops(w2_srcs_h),  # type: ignore[arg-type]
                base_revision_id=p_h_now.active_ontology_rev_id,
                rationale="Commit W2 ontology for R5 out-of-order active_wave_id test.",
            ),
        )

        assert (
            await client.patch(
                "/api/v1/projects/p-r5-h345/settings",
                json={"real_row_proving": False},
            )
        ).status_code == 200
        state["wave"] = 2
        r_w2 = await client.post("/api/v1/projects/p-r5-h345/runs", json={"policy": "balanced"})
        assert r_w2.status_code == 202
        rid_w2 = r_w2.json()["run_id"]
        await wait_run_via_sse(client, f"/api/v1/projects/p-r5-h345/runs/{rid_w2}/events")
        assert (await client.get(f"/api/v1/projects/p-r5-h345/runs/{rid_w2}")).json()[
            "phase"
        ] == "wave_certified"

        w2_active_wave = svc.get_project("p-r5-h345").active_wave_id
        assert w2_active_wave is not None
        assert (await client.get("/api/v1/projects/p-r5-h345/vault/ir")).json()["wave_number"] == 2

        r_ooo = await client.post(
            f"/api/v1/projects/p-r5-h345/runs/{rid_w1}/horizon",
            json={"action": "accept", "notes": "Out-of-order Wave 1 horizon"},
        )
        assert r_ooo.status_code == 202
        await wait_run_via_sse(client, f"/api/v1/projects/p-r5-h345/runs/{rid_w1}/events")

        assert svc.get_project("p-r5-h345").active_wave_id == w2_active_wave
        assert (await client.get("/api/v1/projects/p-r5-h345/vault/ir")).json()["wave_number"] == 2
        _, _, _, resolved_locks, resolved_prior, _, _ = load_verifier_inputs(
            svc.state_manager, svc.workspace_root, "p-r5-h345"
        )
        assert resolved_prior is not None and resolved_prior.wave_number == 2
        assert resolved_locks is not None and resolved_locks.wave_number == 2
