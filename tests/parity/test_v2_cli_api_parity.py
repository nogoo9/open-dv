"""Phase V2-6 M3 tests: UAT-V26-E2E-01 (W001->W002 3-way parity) & UAT-V26-E2E-02 (golden bundle)."""

from __future__ import annotations

import json
import os
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from typer.testing import CliRunner

from opendv.cli.main import app as cli_app
from opendv.cli.mcp_stdio import MCP_TOOL_NAMES
from opendv.models.wire_dtos_discovery import (
    OntologyCommitReq,
    ProjectInitReq,
    ScanApprovalReq,
    SourceAttachReq,
)
from opendv.models.wire_dtos_vault import StorageTargetReq, VaultBuildReq
from opendv.service.app_service import OpenDVService
from opendv.service.guided_translator import contains_forbidden_jargon
from tests.api._api_test_helpers import (
    PUBLIC_AM,
    build_wave1_am_draft,
    build_wave2_am_draft,
    build_wave2_commit_ops,
    copy_public_am_wave1,
    copy_public_am_wave2,
    load_project_profiles,
    wait_run_via_sse,
)
from tests.cli._cli_test_helpers import run_loopback_api_server
from tests.parity._v2_parity_helpers import (
    FIXTURE_TIMESTAMP,
    GOLDEN_BUNDLE_PATH,
    PDF_EXCEPTION_QUOTE,
    build_wave2_hold_draft,
    commit_wave1_v1_ontology,
    create_v2_parity_fake_agy_binary,
    setup_isolated_env,
    snapshot_dir_mtimes,
)


def _cli_json(
    args: list[str], *, env: dict[str, str], expected_exit: int | None = 0
) -> tuple[int, dict[str, Any]]:
    runner = CliRunner()
    res = runner.invoke(cli_app, ["--json", *args], env=env)
    if expected_exit is not None:
        assert res.exit_code == expected_exit, f"CLI {args} exit={res.exit_code}: {res.output}"
    payload = json.loads(res.stdout.strip() or "{}")
    return res.exit_code, payload


async def _mcp_json(session: ClientSession, tool: str, args: dict[str, Any] | None = None) -> tuple[bool, Any]:
    res = await session.call_tool(tool, args or {})
    if len(res.content) > 1:
        items = [json.loads(getattr(c, "text", "{}")) for c in res.content]
        return bool(res.is_error), items
    text = getattr(res.content[0], "text", "{}") if res.content else "{}"
    try:
        return bool(res.is_error), json.loads(text)
    except json.JSONDecodeError:
        return bool(res.is_error), {"raw": text}


async def _mcp_list(
    session: ClientSession, tool: str, args: dict[str, Any] | None = None
) -> tuple[bool, list[dict[str, Any]]]:
    err, data = await _mcp_json(session, tool, args)
    if isinstance(data, list):
        return err, data
    if isinstance(data, dict):
        return err, [data]
    return err, []


def _update_fake_agy_spec(
    spec_dir: Path,
    svc: OpenDVService,
    project_id: str,
    *,
    wave: int,
) -> None:
    spec_path = spec_dir / "fake_agy_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["active_wave"] = wave
    profiles = load_project_profiles(svc, project_id)
    if wave == 1:
        spec["w1_draft"] = build_wave1_am_draft(project_id, "1.0.0", profiles).model_dump(
            mode="json"
        )
    else:
        spec["w2_hold_draft"] = build_wave2_hold_draft(project_id, "1.1.0", profiles).model_dump(
            mode="json"
        )
        spec["w2_draft"] = build_wave2_am_draft(project_id, "1.1.0", profiles).model_dump(
            mode="json"
        )
    spec_path.write_text(json.dumps(spec), encoding="utf-8")


@pytest.mark.asyncio
async def test_uat_v26_e2e_02_golden_bundle_roundtrip_and_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UAT-V26-E2E-02: Golden bundle invariants, conflict matrix, full_demo instant query & tamper."""
    env = setup_isolated_env(tmp_path / "core_rt", monkeypatch)
    host_before = snapshot_dir_mtimes(env.host_local_opendv)

    assert GOLDEN_BUNDLE_PATH.exists(), f"Missing golden bundle {GOLDEN_BUNDLE_PATH}"
    with zipfile.ZipFile(GOLDEN_BUNDLE_PATH) as zf:
        names = zf.namelist()
        uncompressed = sum(info.file_size for info in zf.infolist())
        assert len(names) <= 500
        assert uncompressed <= 250 * 1024 * 1024
        for name in names:
            assert "\\" not in name and not name.startswith("/")
            assert ".." not in PurePosixPath(name).parts
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest["mode"] == "core_project_state"
        assert manifest["created_at"] == FIXTURE_TIMESTAMP
        assert {"project.json", "tables_index.json", "profile.json", "document_map.json"}.issubset(
            set(names)
        )
        blob = b"\n".join(zf.read(n) for n in names).decode("utf-8", errors="ignore")
        assert "C:\\" not in blob
        assert "tmp-build-only" not in blob
        proj_snap = json.loads(zf.read("project.json").decode("utf-8"))
        orig_pid = str(proj_snap["project_id"])
        dbx_sources = [s for s in proj_snap["sources"] if s["kind"] == "databricks_uc"]
        assert dbx_sources and dbx_sources[0]["connector_config"]["secret_refs"] == ["DATABRICKS_TOKEN"]

    svc = env.make_service()
    with run_loopback_api_server(svc) as base_url:
        async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
            # 1. REST multipart import into empty isolated workspace
            resp = await client.post(
                "/api/v1/projects/import",
                data={"conflict": "fail", "trust_workspace": "false"},
                files={
                    "file": (
                        GOLDEN_BUNDLE_PATH.name,
                        GOLDEN_BUNDLE_PATH.read_bytes(),
                        "application/zip",
                    )
                },
            )
            assert resp.status_code == 200, resp.text
            rep = resp.json()
            assert rep["project_id"] == orig_pid
            assert {"READY_OFFLINE", "METADATA_ONLY_BINARY_ABSENT", "NEEDS_LOCAL_SECRET_BINDING"}.issubset(
                set(rep["readiness_flags"])
            )
            assert "DATABRICKS_TOKEN" in rep["missing_secret_refs"]
            assert len(rep["missing_source_ids"]) >= 4

            # 2. Offline Step 3a preview (binaries absent in core_project_state)
            idx_resp = await client.get(f"/api/v1/projects/{orig_pid}/evidence/tables/index")
            assert idx_resp.status_code == 200
            tables = idx_resp.json()
            assert any(t["table_name"] == "holdings_13f" for t in tables)
            hold_tbl = next(t for t in tables if t["table_name"] == "holdings_13f")
            prev_resp = await client.get(
                f"/api/v1/projects/{orig_pid}/evidence/tables/{hold_tbl['table_id']}"
            )
            assert prev_resp.status_code == 200
            assert len(prev_resp.json()["rows"]) > 0

            dm_resp = await client.get(f"/api/v1/projects/{orig_pid}/evidence/documents/map")
            assert dm_resp.status_code == 200
            pdf_map = next(m for m in dm_resp.json() if m["format"] == "pdf")
            assert 4 in pdf_map["approved_scan_pages"]
            assert any(p["page_number"] == 5 and p["canonical_text_sha256"] for p in pdf_map["pages"])
            pages_resp = await client.get(
                f"/api/v1/projects/{orig_pid}/evidence/documents/{pdf_map['doc_id']}/pages",
                params={"page_start": 4, "page_end": 5},
            )
            assert pages_resp.status_code == 409
            assert pages_resp.json()["error_code"] == "SOURCE_BINARY_ABSENT"

            # Offline Step 7 SQL compile from imported CertifiedVaultIR
            comp_resp = await client.post(
                f"/api/v1/projects/{orig_pid}/storage/compile",
                json={"target_id": "duckdb_local", "dbt_sql_dialect": "duckdb", "verify_runtime": False},
            )
            assert comp_resp.status_code == 200, comp_resp.text
            assert any(a["kind"] == "ddl" for a in comp_resp.json()["artifacts"])

            # 3. Conflict matrix across REST JSON path, CLI in-process, CLI --api-url, REST multipart
            fail_json = await client.post(
                "/api/v1/projects/import",
                json={
                    "bundle_path": str(GOLDEN_BUNDLE_PATH),
                    "conflict": "fail",
                    "trust_workspace": False,
                },
            )
            assert fail_json.status_code == 409, fail_json.text

            cli_env = env.subprocess_env()
            ws_bundle = env.ws / GOLDEN_BUNDLE_PATH.name
            ws_bundle.write_bytes(GOLDEN_BUNDLE_PATH.read_bytes())
            _, cli_fail = _cli_json(
                ["project", "import", str(ws_bundle), "--conflict", "fail"],
                env=cli_env,
                expected_exit=4,
            )
            assert cli_fail["ok"] is False

            _, cli_ren = _cli_json(
                [
                    "--api-url",
                    base_url,
                    "project",
                    "import",
                    str(GOLDEN_BUNDLE_PATH),
                    "--conflict",
                    "rename",
                ],
                env=cli_env,
                expected_exit=0,
            )
            assert cli_ren["data"]["project_id"] == f"{orig_pid}-copy"
            assert cli_ren["data"]["resolved_conflict"] == "renamed"

            rep_resp = await client.post(
                "/api/v1/projects/import",
                data={"conflict": "replace", "trust_workspace": "false"},
                files={
                    "file": (
                        GOLDEN_BUNDLE_PATH.name,
                        GOLDEN_BUNDLE_PATH.read_bytes(),
                        "application/zip",
                    )
                },
            )
            assert rep_resp.status_code == 200
            assert rep_resp.json()["resolved_conflict"] == "replaced"

            # 4. Tamper 1 byte -> 422 BUNDLE_HASH_MISMATCH on REST and non-zero on CLI
            tamper_zip = env.ws / "tampered.core.opendv.zip"
            with zipfile.ZipFile(GOLDEN_BUNDLE_PATH, "r") as src_zf, zipfile.ZipFile(
                tamper_zip, "w"
            ) as dst_zf:
                for item in src_zf.infolist():
                    data = src_zf.read(item.filename)
                    if item.filename == "project.json":
                        data = data.rstrip(b"\n") + b" \n"
                    dst_zf.writestr(item.filename, data, compress_type=item.compress_type)

            tamper_resp = await client.post(
                "/api/v1/projects/import",
                data={"conflict": "replace", "trust_workspace": "false"},
                files={"file": (tamper_zip.name, tamper_zip.read_bytes(), "application/zip")},
            )
            assert tamper_resp.status_code == 422, tamper_resp.text
            assert tamper_resp.json()["error_code"] == "BUNDLE_HASH_MISMATCH"
            _, cli_tamper = _cli_json(
                ["project", "import", str(tamper_zip), "--conflict", "replace"],
                env=cli_env,
                expected_exit=None,
            )
            assert cli_tamper["ok"] is False
            assert cli_tamper["error_code"] == "BUNDLE_HASH_MISMATCH"

    # 5. full_demo ZIP_STORED .duckdb instant zero-rebuild query (OPS-02)
    full_env = setup_isolated_env(tmp_path / "full_src", monkeypatch)
    pub_dir = copy_public_am_wave1(full_env.ws)
    profs_box: list[list[Any]] = [[]]
    from tests.helpers import DeterministicMockHarness

    def _full_sel(req: Any) -> dict[str, Any]:
        if req.agent_id == "ship-vault":
            return build_wave1_am_draft("am-full-demo", "1.0.0", profs_box[0]).model_dump(mode="json")
        if req.agent_id == "dv-semantic-critic":
            return {"score": 96, "findings": [], "accepted_waiver_ids": []}
        return {
            "score": 94,
            "classification": "REASONABLY_STABLE",
            "absorbed_natively": [],
            "bucket_a_ops": [],
            "bucket_b_questions": [],
            "bucket_c_watchlist": [],
        }

    full_svc = full_env.make_service(harness=DeterministicMockHarness(selector=_full_sel))
    full_svc.init_project(ProjectInitReq(project_id="am-full-demo", domain_namespace="asset_management"))
    s1 = full_svc.upload_source("am-full-demo", filename="am_sec_treasury_ibor.sqlite", content=(pub_dir / "am_sec_treasury_ibor.sqlite").read_bytes(), kind="sqlite")
    s2 = full_svc.upload_source("am-full-demo", filename="am_esg_stewardship_proxy.parquet", content=(pub_dir / "am_esg_stewardship_proxy.parquet").read_bytes(), kind="parquet")
    s3 = full_svc.upload_source("am-full-demo", filename="am_stewardship_domain_notes.md", content=(pub_dir / "am_stewardship_domain_notes.md").read_bytes(), kind="markdown")
    s4 = full_svc.upload_source("am-full-demo", filename="am_custody_and_voting_guide.pdf", content=(pub_dir / "am_custody_and_voting_guide.pdf").read_bytes(), kind="pdf")
    full_svc.approve_scan_pages("am-full-demo", s4.source_id, ScanApprovalReq(page_numbers=[4], rationale="Approved scan"))
    commit_wave1_v1_ontology(
        full_svc, "am-full-demo", sqlite_sid=s1.source_id, pq_sid=s2.source_id, md_sid=s3.source_id, pdf_sid=s4.source_id
    )
    profs_box[0] = load_project_profiles(full_svc, "am-full-demo")
    await full_svc.build_vault("am-full-demo", VaultBuildReq(policy="balanced"))
    full_svc.migrate_target("am-full-demo", StorageTargetReq(target_id="duckdb_local"))

    full_zip = tmp_path / "am-full-demo.full.opendv.zip"
    _, exp_cli = _cli_json(
        ["project", "export", "--project", "am-full-demo", "--out", str(full_zip), "--full-demo"],
        env=full_env.subprocess_env(),
        expected_exit=0,
    )
    assert exp_cli["data"]["manifest"]["mode"] == "full_demo"
    with zipfile.ZipFile(full_zip) as fzf:
        duck_info = fzf.getinfo("targets/duckdb_local/am-full-demo.duckdb")
        assert duck_info.compress_type == zipfile.ZIP_STORED

    dst_env = setup_isolated_env(tmp_path / "full_dst", monkeypatch)
    dst_svc = dst_env.make_service()
    with run_loopback_api_server(dst_svc) as dst_url:
        async with httpx.AsyncClient(base_url=dst_url, timeout=30.0) as dclient:
            imp_full = await dclient.post(
                "/api/v1/projects/import",
                data={"conflict": "fail", "trust_workspace": "false"},
                files={"file": (full_zip.name, full_zip.read_bytes(), "application/zip")},
            )
            assert imp_full.status_code == 200
            assert imp_full.json()["missing_source_ids"] == []
            assert "READY_OFFLINE" in imp_full.json()["readiness_flags"]

            # Instant query without storage/migrate
            q_resp = await dclient.post(
                "/api/v1/projects/am-full-demo/storage/query",
                json={"sql": "SELECT COUNT(*) AS issuer_cnt FROM raw_vault.hub_issuer", "limit": 10},
            )
            assert q_resp.status_code == 200, q_resp.text
            assert q_resp.json()["rows"][0][0] > 0

        _, q_cli = _cli_json(
            [
                "--api-url",
                dst_url,
                "storage",
                "query",
                "--project",
                "am-full-demo",
                "--sql",
                "SELECT COUNT(*) AS issuer_cnt FROM raw_vault.hub_issuer",
            ],
            env=dst_env.subprocess_env(),
            expected_exit=0,
        )
        assert q_cli["data"]["rows"][0][0] > 0

    assert snapshot_dir_mtimes(env.host_local_opendv) == host_before


@pytest.mark.asyncio
async def test_uat_v26_e2e_01_w001_to_w002_3way_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UAT-V26-E2E-01: W001->W002 across REST (:8420), CLI (in-process & --api-url), and 17-tool stdio MCP."""
    env = setup_isolated_env(tmp_path, monkeypatch)
    host_before = snapshot_dir_mtimes(env.host_local_opendv)
    pub_dir = copy_public_am_wave1(env.ws)
    copy_public_am_wave2(env.ws)

    pid = "am-w1w2-parity"
    spec_dir = tmp_path / "fake_agy"
    spec_dir.mkdir(parents=True, exist_ok=True)
    seed_profiles = []
    from opendv.sources.profiler import profile_source

    seed_profiles.append(profile_source("s_init", "sqlite", str(pub_dir / "am_sec_treasury_ibor.sqlite")))
    fake_agy = create_v2_parity_fake_agy_binary(
        spec_dir,
        w1_draft=build_wave1_am_draft(pid, "1.0.0", seed_profiles).model_dump(mode="json"),
        w2_hold_draft=build_wave1_am_draft(pid, "1.0.0", seed_profiles).model_dump(mode="json"),
        w2_draft=build_wave1_am_draft(pid, "1.0.0", seed_profiles).model_dump(mode="json"),
    )
    monkeypatch.setenv("OPENDV_AGY_CLI_BIN", str(fake_agy))
    cli_env = env.subprocess_env({"OPENDV_AGY_CLI_BIN": str(fake_agy)})

    # Configure fake secret cleanly in user_state_file to verify 0 leakage into artifacts/logs.
    svc = env.make_service(harness=None)
    secret_sentinel = "SECRET-SENTINEL-AM-TEST-DO-NOT-LEAK"
    svc.set_secret("PARITY_DUMMY_TOKEN", secret_sentinel)

    # 1. Init project via CLI in-process
    _cli_json(["project", "init", "--project", pid, "--namespace", "asset_management"], env=cli_env)

    # 2. Wave 1 attach across CLI in-process, CLI --api-url, REST JSON, REST multipart upload
    _cli_json(
        ["source", "attach", "--project", pid, "--kind", "sqlite", "--uri", str(pub_dir / "am_sec_treasury_ibor.sqlite")],
        env=cli_env,
    )

    with run_loopback_api_server(svc) as base_url:
        _cli_json(
            ["--api-url", base_url, "source", "attach", "--project", pid, "--kind", "parquet", "--uri", str(pub_dir / "am_esg_stewardship_proxy.parquet")],
            env=cli_env,
        )
        async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
            md_resp = await client.post(
                f"/api/v1/projects/{pid}/sources",
                json={
                    "kind": "markdown",
                    "uri_or_filename": str(pub_dir / "am_stewardship_domain_notes.md"),
                },
            )
            assert md_resp.status_code == 200, md_resp.text
            pdf_bytes = (PUBLIC_AM / "am_custody_and_voting_guide.pdf").read_bytes()
            pdf_resp = await client.post(
                f"/api/v1/projects/{pid}/sources/upload",
                data={"kind": "pdf"},
                files={"file": ("am_custody_and_voting_guide.pdf", pdf_bytes, "application/pdf")},
            )
            assert pdf_resp.status_code == 200, pdf_resp.text

            srcs_rest = (await client.get(f"/api/v1/projects/{pid}/sources")).json()
            sqlite_sid = next(s["source_id"] for s in srcs_rest if s["kind"] == "sqlite")
            pq_sid = next(s["source_id"] for s in srcs_rest if s["kind"] == "parquet")
            md_sid = next(s["source_id"] for s in srcs_rest if s["kind"] == "markdown")
            pdf_sid = next(s["source_id"] for s in srcs_rest if s["kind"] == "pdf")

            # Verify PDF page 4 fail-closed guard (422 UNREDACTED_SCAN_BLOCKED) -> approve-scan -> 200
            p4_before = await client.get(
                f"/api/v1/projects/{pid}/evidence/documents/{pdf_sid}/pages",
                params={"page_start": 4, "page_end": 4},
            )
            assert p4_before.status_code == 422
            assert p4_before.json()["error_code"] == "UNREDACTED_SCAN_BLOCKED"

            _cli_json(
                [
                    "--api-url",
                    base_url,
                    "source",
                    "approve-scan",
                    "--project",
                    pid,
                    "--doc",
                    pdf_sid,
                    "--pages",
                    "4",
                    "--rationale",
                    "Steward approved Custody guide scanned page 4",
                ],
                env=cli_env,
            )
            p4_after = await client.get(
                f"/api/v1/projects/{pid}/evidence/documents/{pdf_sid}/pages",
                params={"page_start": 4, "page_end": 5},
            )
            assert p4_after.status_code == 200
            assert p4_after.json()[0]["scan_approved"] is True
            assert PDF_EXCEPTION_QUOTE in p4_after.json()[1]["masked_text"]
            dm_rest = (await client.get(f"/api/v1/projects/{pid}/evidence/documents/map")).json()
            pdf_dm = next(d for d in dm_rest if d["doc_id"] == pdf_sid)
            assert pdf_dm["approved_scan_pages"] == [4]

            # Citation occurrence=2 check via REST
            cite2 = await client.post(
                f"/api/v1/projects/{pid}/ontology/citations/resolve",
                json={
                    "doc_id": pdf_sid,
                    "page_number": 5,
                    "verbatim_quote": PDF_EXCEPTION_QUOTE,
                    "occurrence": 2,
                },
            )
            assert cite2.status_code == 200
            assert cite2.json()["matched"] is True and cite2.json()["char_start"] > 0

            # Start 17-tool stdio MCP server and verify Wave 1 + Step 4 hold->answer
            mcp_params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "opendv.cli", "mcp", "serve", "--transport", "stdio", "--project", pid],
                env=cli_env,
            )
            async with stdio_client(mcp_params) as (read_stream, write_stream), ClientSession(
                read_stream, write_stream
            ) as mcp:
                await mcp.initialize()
                listed = await mcp.list_tools()
                assert {t.name for t in listed.tools} == set(MCP_TOOL_NAMES)
                assert len(listed.tools) == 17

                err, mcp_proj = await _mcp_json(mcp, "project_get")
                assert not err and mcp_proj["project_id"] == pid

                err, mcp_srcs = await _mcp_list(mcp, "source_list")
                assert not err and {s["source_id"] for s in mcp_srcs} == {
                    s["source_id"] for s in srcs_rest
                }
                _, cli_srcs = _cli_json(["source", "list", "--project", pid], env=cli_env)
                assert {s["source_id"] for s in cli_srcs["data"]} == {
                    s["source_id"] for s in srcs_rest
                }

                err, _ = await _mcp_json(
                    mcp,
                    "concept_memory_update",
                    {"term": "ProxyBallot", "state": "PINNED", "concept_uri": "urn:asset_mgmt:ProxyBallot"},
                )
                assert not err

                # Step 4 hold -> answer parity across MCP + REST
                err, run4 = await _mcp_json(
                    mcp,
                    "ontology_construct",
                    {"discovery_mode": "incremental_new", "hint": "trigger-hold"},
                )
                assert not err and run4["status"] == "HOLD_CHECKPOINTED"
                err, ans4 = await _mcp_json(
                    mcp,
                    "ontology_answer",
                    {"run_id": run4["run_id"], "answers": {"q_step4_bk": "ballot_id"}},
                )
                assert not err and ans4["status"] == "COMPLETED"

                # Commit seed 0.1.0 then promote to v1.0.0
                rev1_id = commit_wave1_v1_ontology(
                    svc,
                    pid,
                    sqlite_sid=sqlite_sid,
                    pq_sid=pq_sid,
                    md_sid=md_sid,
                    pdf_sid=pdf_sid,
                )
                ont_bundle = (await client.get(f"/api/v1/projects/{pid}/ontology")).json()
                assert ont_bundle["ontology"]["ontology_version"] == "1.0.0"

                err, srch = await _mcp_list(mcp, "ontology_search", {"query": "Treasury"})
                assert not err and len(srch) > 0

                # Update fake_agy_spec with real W1 source IDs -> Step 6 W001 via REST + SSE -> V001 CLI migrate
                _update_fake_agy_spec(spec_dir, svc, pid, wave=1)
                vb1_resp = await client.post(
                    f"/api/v1/projects/{pid}/runs", json={"policy": "balanced"}
                )
                assert vb1_resp.status_code in (200, 202), vb1_resp.text
                vb1_run_id = vb1_resp.json()["run_id"]
                sse1 = await wait_run_via_sse(
                    client, f"/api/v1/projects/{pid}/runs/{vb1_run_id}/events"
                )
                assert (
                    sse1 and any(e.get("event") in {"RUN_COMPLETED", "run_completed"} for e in sse1)
                ) or (
                    await client.get(f"/api/v1/projects/{pid}/runs/{vb1_run_id}")
                ).json()["status"] == "COMPLETED"

                # 3-way vault_verify parity
                v_rest = (await client.post(f"/api/v1/projects/{pid}/vault/verify")).json()
                _, v_cli = _cli_json(["--api-url", base_url, "vault", "verify", "--project", pid], env=cli_env)
                err, v_mcp = await _mcp_json(mcp, "vault_verify")
                assert v_rest["passed"] and v_cli["data"]["passed"] and not err and v_mcp["passed"]

                # Step 7 V001 compile + migrate via CLI --api-url
                _cli_json(
                    ["--api-url", base_url, "storage", "compile", "--project", pid, "--target", "duckdb_local"],
                    env=cli_env,
                )
                _, m1_cli = _cli_json(
                    ["--api-url", base_url, "storage", "migrate", "--project", pid, "--target", "duckdb_local"],
                    env=cli_env,
                )
                assert m1_cli["data"]["wave_number"] == 1 and m1_cli["data"]["rows_loaded"] > 0

                # 3. Wave 2 attach -> NEW/UNCHANGED -> 409 stale OCC -> 422 ONT_EVO_CONTRACT_VIOLATION -> v1.1.0
                for kind, fn in (
                    ("parquet", "wave2_eu_custody_lei_and_drift.parquet"),
                    ("csv", "wave2_bitemporal_nav_restatements.csv"),
                    ("csv", "wave2_ambiguous_otc_collateral.csv"),
                    ("csv", "prime_allocations_w2.csv"),
                ):
                    r_att = await client.post(
                        f"/api/v1/projects/{pid}/sources",
                        json={"kind": kind, "uri_or_filename": str(pub_dir / fn)},
                    )
                    assert r_att.status_code == 200, r_att.text

                err, idx_w2_mcp = await _mcp_list(mcp, "evidence_tables_index")
                assert not err
                badges = {t["table_name"]: t["badge"] for t in idx_w2_mcp}
                assert badges["wave2_eu_custody_lei_and_drift"] == "NEW"
                assert badges["wave2_bitemporal_nav_restatements"] == "NEW"
                assert badges["holdings_13f"] == "UNCHANGED"

                # Confirm PII masking on wave2_ambiguous_otc_collateral sample values (<MASKED_PII>)
                otc_tid = next(t["table_id"] for t in idx_w2_mcp if t["table_name"] == "wave2_ambiguous_otc_collateral")
                otc_prev = (await client.get(f"/api/v1/projects/{pid}/evidence/tables/{otc_tid}")).json()
                assert "<MASKED_PII>" in json.dumps(otc_prev)
                assert "021000021" not in json.dumps(otc_prev)

                # 409 Stale OCC on REST + CLI
                stale_req = OntologyCommitReq(
                    base_revision_id="rev-stale-mismatch",
                    operations=[],
                    rationale="Attempt stale base_revision_id commit",
                )
                r409 = await client.post(
                    f"/api/v1/projects/{pid}/ontology/commit",
                    json=stale_req.model_dump(mode="json"),
                )
                assert r409.status_code == 409 and r409.json()["error_code"] == "REVISION_CONFLICT"
                stale_yaml = tmp_path / "stale.yaml"
                stale_yaml.write_text(
                    (await client.get(f"/api/v1/projects/{pid}/ontology/export")).text,
                    encoding="utf-8",
                )
                _, cli_409 = _cli_json(
                    [
                        "--api-url",
                        base_url,
                        "ontology",
                        "commit",
                        "--project",
                        pid,
                        "--yaml",
                        str(stale_yaml),
                        "--base-revision",
                        "rev-stale-mismatch",
                        "--rationale",
                        "Stale revision CLI check",
                    ],
                    env=cli_env,
                    expected_exit=None,
                )
                assert cli_409["ok"] is False and cli_409["error_code"] == "REVISION_CONFLICT"

                # 422 ONT_EVO_CONTRACT_VIOLATION deleting locked W001 concept (Issuer)
                brk_ops = [
                    {
                        "op_id": "op_brk_issuer",
                        "op_type": "DeleteConcept",
                        "concept_uri": "urn:asset_mgmt:Issuer",
                        "rationale": "Attempt breaking deletion of W001 locked Issuer BK",
                    }
                ]
                r422 = await client.post(
                    f"/api/v1/projects/{pid}/ontology/commit",
                    json={
                        "base_revision_id": rev1_id,
                        "operations": brk_ops,
                        "rationale": "Attempt breaking ontology commit",
                    },
                )
                assert r422.status_code == 422, r422.text
                assert r422.json()["error_code"] == "ONT_EVO_CONTRACT_VIOLATION"
                err_mcp_brk, res_mcp_brk = await _mcp_json(
                    mcp,
                    "ontology_commit",
                    {
                        "base_revision_id": rev1_id,
                        "operations": brk_ops,
                        "rationale": "Attempt breaking MCP commit",
                    },
                )
                assert err_mcp_brk and "ONT_EVO_CONTRACT_VIOLATION" in json.dumps(res_mcp_brk)

                # Additive Wave 2 commit -> v1.1.0
                all_srcs = (await client.get(f"/api/v1/projects/{pid}/sources")).json()
                w2_commit = await client.post(
                    f"/api/v1/projects/{pid}/ontology/commit",
                    json={
                        "base_revision_id": rev1_id,
                        "operations": [o.model_dump(mode="json") for o in build_wave2_commit_ops(all_srcs)],
                        "rationale": "Wave 2 asset management ontology evolution to v1.1.0",
                        "low_confidence_grounding_waiver": True,
                        "grounding_rationale": "Wave 2 public_am sources grounded and steward approved",
                        "waive_quarantine": True,
                    },
                )
                assert w2_commit.status_code == 200, w2_commit.text
                assert w2_commit.json()["ontology_version"] == "1.1.0"

                # 4. Step 6 W002: L3 hold (uti_id vs isda_master_id) -> guided view -> V8 Horizon steer -> sal_issuer
                _update_fake_agy_spec(spec_dir, svc, pid, wave=2)
                vb2_start = await client.post(
                    f"/api/v1/projects/{pid}/runs", json={"policy": "balanced"}
                )
                assert vb2_start.status_code in (200, 202), vb2_start.text
                vb2_rid = vb2_start.json()["run_id"]

                # Drain until L3 HOLD_CHECKPOINTED
                await wait_run_via_sse(client, f"/api/v1/projects/{pid}/runs/{vb2_rid}/events")
                run_w2 = (await client.get(f"/api/v1/projects/{pid}/runs/{vb2_rid}")).json()
                assert run_w2["status"] == "HOLD_CHECKPOINTED"
                assert any(q["question_id"] == "q_otc_key" for q in run_w2["pending_questions"])

                guided = (await client.get(f"/api/v1/projects/{pid}/runs/{vb2_rid}/guided-view")).json()
                assert not contains_forbidden_jargon(json.dumps(guided))

                # Answer L3 hold via REST -> hits V8 Horizon Hold (hq_w2_horizon)
                dec_resp = await client.post(
                    f"/api/v1/projects/{pid}/runs/{vb2_rid}/decisions",
                    json={"answers": {"q_otc_key": "uti_id"}},
                )
                assert dec_resp.status_code in (200, 202), dec_resp.text
                await wait_run_via_sse(client, f"/api/v1/projects/{pid}/runs/{vb2_rid}/events")
                run_after_l3 = (await client.get(f"/api/v1/projects/{pid}/runs/{vb2_rid}")).json()
                assert run_after_l3["status"] == "HOLD_CHECKPOINTED"

                # Steer V8 Horizon via MCP vault_horizon -> COMPLETED
                err, h_done = await _mcp_json(
                    mcp,
                    "vault_horizon",
                    {
                        "run_id": vb2_rid,
                        "action": "steer",
                        "answers": {"hq_w2_horizon": "keep_uti_id"},
                        "notes": "Confirm global uti_id for future OTC derivative waves",
                    },
                )
                assert not err and h_done["status"] == "COMPLETED", h_done

                ir_w2 = (await client.get(f"/api/v1/projects/{pid}/vault/ir")).json()
                assert ir_w2["wave_number"] == 2
                assert any(lnk["link_name"] == "sal_issuer" and lnk["link_kind"] == "SAME_AS" for lnk in ir_w2["links"])

                # 5. Step 7 V002 incremental migration (0 Raw Vault DROP/ALTER) + regenerate parity
                mig2 = await client.post(
                    f"/api/v1/projects/{pid}/storage/migrate", json={"target_id": "duckdb_local"}
                )
                assert mig2.status_code == 200, mig2.text
                assert mig2.json()["wave_number"] == 2 and mig2.json()["verified"] is True

                v002_sql_path = (
                    env.opendv_home / "projects" / pid / "targets" / "duckdb_local" / "migrations" / "V002_migrate.sql"
                )
                v002_sql = v002_sql_path.read_text(encoding="utf-8")
                assert not re.search(
                    r"\b(DROP\s+TABLE|DROP\s+COLUMN|ALTER\s+TABLE\s+raw_vault\.)",
                    v002_sql,
                    re.IGNORECASE,
                ), f"Unexpected destructive DDL in V002_migrate.sql:\n{v002_sql}"

                col_sql = (
                    "SELECT table_schema, table_name, column_name, data_type "
                    "FROM information_schema.columns "
                    "WHERE table_schema IN ('raw_vault','business_vault','staging','bronze','information_mart','reference') "
                    "ORDER BY 1, 2, 3"
                )
                cols_before = (
                    await client.post(
                        f"/api/v1/projects/{pid}/storage/query",
                        json={"sql": col_sql, "limit": 2000},
                    )
                ).json()["rows"]

                regen = await client.post(
                    f"/api/v1/projects/{pid}/storage/regenerate",
                    json={"target_id": "duckdb_local"},
                )
                assert regen.status_code == 200 and regen.json()["verified"] is True
                cols_after = (
                    await client.post(
                        f"/api/v1/projects/{pid}/storage/query",
                        json={"sql": col_sql, "limit": 2000},
                    )
                ).json()["rows"]
                assert cols_before == cols_after

                # 6. Bi-temporal SQL query parity (216.28 vs 245.0 vs 156.25) across REST, CLI, and MCP
                nav_sql = (
                    "SELECT nav_per_share, applied_dts, restatement_reason "
                    "FROM raw_vault.sat_nav_bitemp "
                    "WHERE CAST(nav_per_share AS DOUBLE) IN (216.28, 245.0, 156.25) "
                    "ORDER BY applied_dts, nav_per_share"
                )
                q_rest = (
                    await client.post(
                        f"/api/v1/projects/{pid}/storage/query",
                        json={"sql": nav_sql, "limit": 20},
                    )
                ).json()
                _, q_cli_local = _cli_json(
                    ["storage", "query", "--project", pid, "--sql", nav_sql],
                    env=cli_env,
                )
                _, q_cli_remote = _cli_json(
                    ["--api-url", base_url, "storage", "query", "--project", pid, "--sql", nav_sql],
                    env=cli_env,
                )
                err, q_mcp = await _mcp_json(mcp, "storage_query", {"sql": nav_sql, "limit": 20})
                assert not err

                nav_rest_vals = [float(r[0]) for r in q_rest["rows"]]
                assert nav_rest_vals == [216.28, 245.0, 156.25]
                assert q_cli_local["data"]["rows"] == q_rest["rows"]
                assert q_cli_remote["data"]["rows"] == q_rest["rows"]
                assert q_mcp["rows"] == q_rest["rows"]

                # Verify 0 leaked secrets across SSE logs, target artifacts, and workspace files
                logs_rest = (await client.get(f"/api/v1/projects/{pid}/runs/{vb2_rid}/logs")).text
                assert secret_sentinel not in logs_rest
                proj_blob = "\n".join(
                    p.read_text(encoding="utf-8", errors="ignore")
                    for p in (env.opendv_home / "projects" / pid).rglob("*")
                    if p.is_file() and p.suffix in {".json", ".jsonl", ".sql", ".yaml", ".yml"}
                )
                assert secret_sentinel not in proj_blob

    assert snapshot_dir_mtimes(env.host_local_opendv) == host_before
