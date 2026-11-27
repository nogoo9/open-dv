"""Non-live M4 tests for Step-4 MCP `ground_columns`, Async REST, Security, and CLI (46 -> 48)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import opendv.service.bundles.zip_guard as zip_guard
from opendv.api.server import create_app
from opendv.cli.export_contracts import build_cli_spec
from opendv.cli.main import build_cli_app
from opendv.models import (
    AddConcept,
    ClientAdditiveOp,
    ColumnProfile,
    ConceptAttribute,
    ConceptTableBinding,
    ConnectorConfigPayload,
    ErrorCode,
    ExternalMapping,
    GroundingRequest,
    GroundingResponse,
    HarnessBudget,
    OntologyCommitReq,
    OntologyGroundReq,
    OpenDVError,
    ProjectInitReq,
    SourceAttachReq,
    SourceProfile,
    TableProfile,
)
from opendv.models.grounding import ColumnEvidence, GroundingPolicy, TableEvidence
from opendv.models.sources import ColumnShapeStats
from opendv.service._mcp_grounding_tool import coord_ground_columns
from opendv.service._mcp_ports import BrokerBudgetTracker, BrokerHoldCoordinator
from opendv.service._mcp_scout_tools import scout_inspect_table_profile
from opendv.service._mcp_tools import (
    BrokerToolContext,
    coord_propose_ontology_delta,
)
from opendv.service.app_service import OpenDVService
from opendv.service.state_store import ProjectStateManager
from tests.cli._cli_test_helpers import run_loopback_api_server
from tests.helpers.mock_harness import InMemoryRunEventSink
from tests.service.test_mcp_broker_and_external import (
    FakeBm25Matcher,
    InMemoryBrokerStateStore,
)

_FIBO_BASE = "https://spec.edmcouncil.org/fibo/ontology"


def _jcs_bytes(payload: dict[str, Any]) -> bytes:
    """Serialize dict as canonical RFC 8785 JCS UTF-8 bytes with zero trailing newline."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _minimal_pack_dict(*, deprecated_bond: bool = False) -> dict[str, Any]:
    bond_uri = f"{_FIBO_BASE}/SEC/Debt/Bonds/Bond"
    acct_uri = f"{_FIBO_BASE}/FBC/Accounts/Account"
    return {
        "metadata": {
            "pack_id": "fibo-test-v2",
            "pack_schema_version": "2.0.0",
            "version": "2.0.0-test",
            "modules": ["BE", "FBC", "FND", "SEC"],
        },
        "classes": {
            bond_uri: {
                "uri": bond_uri,
                "label": "Bond",
                "definition": "A negotiable debt security obligation.",
                "module": "SEC",
                "synonyms": ["bond security", "fixed income bond", "bond_id"],
                "sub_class_of": [],
                "archetype": "entity",
                "deprecated": deprecated_bond,
                "replaced_by": acct_uri if deprecated_bond else None,
            },
            acct_uri: {
                "uri": acct_uri,
                "label": "Account",
                "definition": "A financial account maintaining balances.",
                "module": "FBC",
                "synonyms": ["account", "account_id"],
                "sub_class_of": [],
                "archetype": "entity",
                "deprecated": False,
                "replaced_by": None,
            },
        },
        "properties": {
            f"{_FIBO_BASE}/FBC/DebtAndEquities/Debt/hasMaturityDate": {
                "uri": f"{_FIBO_BASE}/FBC/DebtAndEquities/Debt/hasMaturityDate",
                "label": "maturity date",
                "definition": "Contractual date on which principal repayment is due.",
                "module": "FBC",
                "synonyms": ["maturity_date", "mat_dt"],
                "property_type": "DatatypeProperty",
                "domain": [bond_uri],
                "range": ["xsd:date"],
                "sub_property_of": [],
                "inverse_of": [],
                "is_functional": True,
                "is_inverse_functional": False,
                "deprecated": False,
            },
            f"{_FIBO_BASE}/SEC/Equities/EquityInstruments/hasCUSIP": {
                "uri": f"{_FIBO_BASE}/SEC/Equities/EquityInstruments/hasCUSIP",
                "label": "CUSIP identifier",
                "definition": "Nine-character North American security identifier.",
                "module": "SEC",
                "synonyms": ["cusip", "security_cusip"],
                "property_type": "DatatypeProperty",
                "domain": [bond_uri],
                "range": ["xsd:string"],
                "sub_property_of": [],
                "inverse_of": [],
                "is_functional": True,
                "is_inverse_functional": False,
                "deprecated": False,
            },
        },
        "relations": [],
        "individuals": {},
    }


def _sample_ground_req(
    table_name: str = "holdings",
    *,
    deterministic: bool = True,
) -> GroundingRequest:
    kwargs: dict[str, Any] = {
        "tables": [
            TableEvidence(
                table_id=f"tbl_{table_name}",
                table_name=table_name,
                columns=[
                    ColumnEvidence(
                        column_name="cusip",
                        data_type="VARCHAR",
                        is_pk=True,
                    ),
                    ColumnEvidence(
                        column_name="maturity_date",
                        data_type="DATE",
                    ),
                ],
            )
        ]
    }
    if deterministic:
        kwargs["policy"] = GroundingPolicy(mode="deterministic_only", skip_reason="nonlive_test")
    return GroundingRequest(**kwargs)


def _sample_table_profile(
    table_id: str,
    table_name: str,
    col_names: list[str],
    *,
    with_shape: bool = False,
) -> TableProfile:
    cols = []
    for idx, cname in enumerate(col_names):
        shape = (
            ColumnShapeStats(
                profiled_non_null_count=200 if cname == "cusip" else 20,
                distinct_ratio=0.95 if cname == "cusip" else 0.9,
                surrogate_kind="none",
                structural_patterns={"cusip": 1.0} if cname == "cusip" else {},
            )
            if with_shape
            else None
        )
        cols.append(
            ColumnProfile(
                col_name=cname,
                sql_type="DATE" if "date" in cname else "VARCHAR",
                nullable=False,
                null_ratio=0.0,
                is_pk=(idx == 0),
                distinct_count_est=20,
                sample_values=[],
                shape_stats=shape,
            )
        )
    return TableProfile(
        table_id=table_id,
        table_name=table_name,
        row_count_est=20,
        columns=cols,
    )


def _make_broker_ctx(
    *,
    num_tables: int = 1,
    cols_per_table: int = 3,
    budget: HarnessBudget | None = None,
    with_shape: bool = False,
    discovery_mode: str = "incremental_new",
) -> tuple[BrokerToolContext, InMemoryBrokerStateStore]:
    store = InMemoryBrokerStateStore("p-gnd")
    for t_idx in range(num_tables):
        tid = f"tbl_{t_idx}"
        tname = "holdings" if t_idx == 0 else f"positions_{t_idx}"
        base_cols = ["cusip", "maturity_date", "account_id"]
        extra = [f"col_extra_{c}" for c in range(max(0, cols_per_table - len(base_cols)))]
        col_names = (base_cols + extra)[:cols_per_table]
        tbl = _sample_table_profile(tid, tname, col_names, with_shape=with_shape)
        store.add_source(
            source_id=f"src_{t_idx}",
            kind="csv",
            uri=f"mem://{tname}.csv",
            status="READY_OFFLINE",
            profile=SourceProfile(
                source_id=f"src_{t_idx}",
                schema_fingerprint=f"fp-{t_idx}-" + ("a" * 56),
                table_count=1,
                tables=[tbl],
            ),
        )
    sink = InMemoryRunEventSink()
    effective_budget = budget or HarnessBudget(
        max_turns=24, max_tokens=300_000, timeout_seconds=600, max_broker_calls=64
    )
    ctx = BrokerToolContext(
        project_id="p-gnd",
        run_id="run4-test-1",
        state_store=store,
        bm25_matcher=FakeBm25Matcher(),
        budget_tracker=BrokerBudgetTracker(budget=effective_budget),
        hold_coord=BrokerHoldCoordinator(
            project_id="p-gnd",
            run_id="run4-test-1",
            state_store=store,
            event_sink=sink,
        ),
        discovery_mode=discovery_mode,  # type: ignore[arg-type]
    )
    return ctx, store


def _make_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> OpenDVService:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    local_app = tmp_path / "localapp"
    local_app.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_HOME", str(ws / ".opendv"))
    monkeypatch.setenv("LOCALAPPDATA", str(local_app))
    monkeypatch.setenv("XDG_DATA_HOME", str(local_app))
    return OpenDVService(ws, state_dir=local_app / "opendv")


# ---------------------------------------------------------------------------
# 1. CLI Contract Parity (61 commands) + `ontology ground` + `ontology fibo-sync`
# ---------------------------------------------------------------------------


def test_cli_spec_has_48_subcommands_including_ground_and_fibo_sync() -> None:
    spec = build_cli_spec()
    subcommands = spec["subcommands"]
    assert len(subcommands) == 61
    by_cmd = {" ".join(entry["path"]): entry for entry in subcommands}
    assert by_cmd["ontology ground"]["http_method"] == "POST"
    assert by_cmd["ontology ground"]["http_route"] == "/api/v1/ontology/ground"
    assert by_cmd["ontology fibo-sync"]["http_method"] is None
    assert by_cmd["ontology fibo-sync"]["http_route"] is None


def test_cli_ontology_ground_input_glossary_plain_and_validation_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _make_service(tmp_path, monkeypatch)
    req_path = svc.workspace_root / "ground_req.json"
    req_path.write_text(
        _sample_ground_req(deterministic=False).model_dump_json(exclude_defaults=True),
        encoding="utf-8",
    )
    glossary_path = svc.workspace_root / "glossary.yaml"
    glossary_path.write_text(
        "version: 1\n"
        "terms:\n"
        "  cusip:\n"
        "    label: CUSIP Security Identifier\n"
        "    definition: Unique security CUSIP code.\n"
        "    aliases: [cusip]\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    app = build_cli_app()

    # Plain-text table output + glossary knowledge source
    res_plain = runner.invoke(
        app,
        [
            "ontology",
            "ground",
            "--input",
            str(req_path),
            "--knowledge-source",
            "glossary",
            "--glossary",
            str(glossary_path),
            "--mode",
            "deterministic_only",
            "--skip-reason",
            "plain_table_smoke",
        ],
    )
    assert res_plain.exit_code == 0, res_plain.stdout
    assert "cusip" in res_plain.stdout
    assert "Top Match" in res_plain.stdout
    assert "\x1b[" not in res_plain.stdout

    # --json deterministic_only with --skip-reason
    res_json = runner.invoke(
        app,
        [
            "--json",
            "ontology",
            "ground",
            "--input",
            str(req_path),
            "--mode",
            "deterministic_only",
            "--skip-reason",
            "ci_smoke_check",
        ],
    )
    assert res_json.exit_code == 0, res_json.stdout
    envelope = json.loads(res_json.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["mode_used"] == "deterministic_only"
    assert len(envelope["data"]["tables"]) == 1

    # XOR errors: neither --project nor --input, or both
    res_neither = runner.invoke(app, ["--json", "ontology", "ground"])
    assert res_neither.exit_code != 0
    assert json.loads(res_neither.stdout)["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    res_both = runner.invoke(
        app,
        ["--json", "ontology", "ground", "--project", "p1", "--input", str(req_path)],
    )
    assert res_both.exit_code != 0
    assert json.loads(res_both.stdout)["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    # Missing input file -> RESOURCE_NOT_FOUND
    res_missing = runner.invoke(
        app, ["--json", "ontology", "ground", "--input", str(svc.workspace_root / "missing.json")]
    )
    assert res_missing.exit_code != 0
    assert json.loads(res_missing.stdout)["error_code"] == "RESOURCE_NOT_FOUND"

    # Blank --skip-reason under deterministic_only -> SYNTAX_INVALID_ARGUMENT
    res_blank_skip = runner.invoke(
        app,
        [
            "--json",
            "ontology",
            "ground",
            "--input",
            str(req_path),
            "--mode",
            "deterministic_only",
            "--skip-reason",
            "   ",
        ],
    )
    assert res_blank_skip.exit_code != 0
    assert json.loads(res_blank_skip.stdout)["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    # Non-empty --skip-reason under agentic -> SYNTAX_INVALID_ARGUMENT
    res_conflict_mode = runner.invoke(
        app,
        [
            "--json",
            "ontology",
            "ground",
            "--input",
            str(req_path),
            "--mode",
            "agentic",
            "--skip-reason",
            "should_not_be_set",
        ],
    )
    assert res_conflict_mode.exit_code != 0
    assert json.loads(res_conflict_mode.stdout)["error_code"] == "SYNTAX_INVALID_ARGUMENT"


def test_cli_ontology_ground_project_and_remote_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _make_service(tmp_path, monkeypatch)
    monkeypatch.chdir(svc.workspace_root)
    proj = svc.init_project(
        ProjectInitReq(project_id="cli-gnd-proj", domain_namespace="asset_management")
    )
    csv_path = svc.workspace_root / "bond_book.csv"
    csv_path.write_text(
        "cusip,maturity_date\n037833100,2030-05-15\n594918104,2031-06-01\n",
        encoding="utf-8",
    )
    svc.attach_source(
        proj.project_id, SourceAttachReq(kind="csv", uri_or_filename="bond_book.csv")
    )

    runner = CliRunner()
    app = build_cli_app()
    with run_loopback_api_server(svc) as base_url:
        res_remote = runner.invoke(
            app,
            [
                "--json",
                "--api-url",
                base_url,
                "ontology",
                "ground",
                "--project",
                proj.project_id,
                "--mode",
                "deterministic_only",
                "--skip-reason",
                "remote_loopback_test",
            ],
        )
        assert res_remote.exit_code == 0, res_remote.stdout
        data = json.loads(res_remote.stdout)["data"]
        assert data["tables"][0]["columns"][0]["column_name"] == "cusip"


def test_execute_ground_ontology_bumps_and_restores_remote_timeout() -> None:
    from opendv.cli._client_grounding import execute_ground_ontology

    seen_timeouts: list[float] = []

    class _FakeResp:
        def __init__(self, body: dict[str, Any]) -> None:
            self._body = body

        def json(self) -> dict[str, Any]:
            return self._body

    class _FakeRemoteClient:
        is_remote = True
        timeout_seconds = 120.0

        def service(self) -> Any:
            raise AssertionError("service() should not be called in remote mode")

        def _http(
            self,
            method: str,
            path: str,
            *,
            json_body: Any = None,
        ) -> _FakeResp:
            assert method == "POST"
            assert path == "/api/v1/ontology/ground"
            seen_timeouts.append(self.timeout_seconds)
            if json_body.get("project_id") == "fail":
                raise RuntimeError("boom")
            return _FakeResp(
                {
                    "knowledge_source_id": "fibo",
                    "knowledge_source_version": "2.0.0",
                    "knowledge_source_sha256": "a" * 64,
                    "engine_version": "2.0.0",
                    "mode_used": "deterministic_only",
                    "skip_reason": "unit",
                    "tables": [],
                }
            )

    client = _FakeRemoteClient()
    req = OntologyGroundReq(project_id="ok")
    resp = execute_ground_ontology(client, req)
    assert resp.knowledge_source_id == "fibo"
    assert seen_timeouts == [900.0]
    assert client.timeout_seconds == 120.0

    with pytest.raises(RuntimeError, match="boom"):
        execute_ground_ontology(client, OntologyGroundReq(project_id="fail"))
    assert seen_timeouts == [900.0, 900.0]
    assert client.timeout_seconds == 120.0


def test_cli_ontology_fibo_sync_local_drift_repin_and_remote_reject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _make_service(tmp_path, monkeypatch)
    monkeypatch.chdir(svc.workspace_root)
    proj = svc.init_project(
        ProjectInitReq(project_id="sync-proj", domain_namespace="asset_management")
    )
    bond_uri = f"{_FIBO_BASE}/SEC/Debt/Bonds/Bond"
    acct_uri = f"{_FIBO_BASE}/FBC/Accounts/Account"

    pack_v1 = tmp_path / "pack_v1.json"
    pack_v1.write_bytes(_jcs_bytes(_minimal_pack_dict(deprecated_bond=False)))

    runner = CliRunner()
    app = build_cli_app()
    res_install = runner.invoke(
        app,
        ["--json", "ontology", "fibo-sync", "--pack-file", str(pack_v1), "--tag", "v2026q1"],
    )
    assert res_install.exit_code == 0, res_install.stdout
    sha_v1 = json.loads(res_install.stdout)["data"]["pack_sha256"]

    svc.commit_ontology(
        proj.project_id,
        OntologyCommitReq(
            operations=[
                AddConcept(
                    concept_uri="urn:opendv:asset_management:bond",
                    concept_name="Bond",
                    description="Debt security",
                    key_authority_tier="TIER_1_GLOBAL_STANDARD",
                    external_mappings=[
                        ExternalMapping(
                            standard_uri=bond_uri,
                            reference_pack_id="fibo-test-v2",
                            reference_pack_sha256=sha_v1,
                            mapping_type="skos:exactMatch",
                            confidence=Decimal("0.9000"),
                        )
                    ],
                    table_bindings=[
                        ConceptTableBinding(
                            source_id="src_1",
                            table_id="tbl_1",
                            col_name="cusip",
                        )
                    ],
                    attributes=[
                        ConceptAttribute(
                            name="cusip",
                            logical_type="UTF8_STRING",
                            sql_type_hint="VARCHAR",
                            nullable=False,
                            is_business_key=True,
                            is_pii=False,
                        )
                    ],
                )
            ],
            base_revision_id=proj.active_ontology_rev_id,
            rationale="seed for fibo-sync",
        ),
    )

    pack_v2 = tmp_path / "pack_v2.json"
    pack_v2.write_bytes(_jcs_bytes(_minimal_pack_dict(deprecated_bond=True)))
    res_repin = runner.invoke(
        app,
        [
            "ontology",
            "fibo-sync",
            "--pack-file",
            str(pack_v2),
            "--tag",
            "v2026q2",
            "--check-project",
            proj.project_id,
            "--apply-repin",
        ],
    )
    assert res_repin.exit_code == 0, res_repin.stdout
    assert "deprecated_with_replacement" in res_repin.stdout
    assert "repin_deltas_applied=1" in res_repin.stdout

    bundle = svc.get_ontology_bundle(proj.project_id)
    assert bundle.ontology.concepts[0].external_mappings[0].standard_uri == acct_uri

    res_empty = runner.invoke(app, ["--json", "ontology", "fibo-sync"])
    assert res_empty.exit_code != 0
    assert json.loads(res_empty.stdout)["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    res_repin_only = runner.invoke(app, ["--json", "ontology", "fibo-sync", "--apply-repin"])
    assert res_repin_only.exit_code != 0
    assert json.loads(res_repin_only.stdout)["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    res_remote = runner.invoke(
        app,
        [
            "--json",
            "--api-url",
            "http://127.0.0.1:9999",
            "ontology",
            "fibo-sync",
            "--check-project",
            "p",
        ],
    )
    assert res_remote.exit_code != 0
    assert json.loads(res_remote.stdout)["error_code"] == "SYNTAX_INVALID_ARGUMENT"


# ---------------------------------------------------------------------------
# 2. Async REST `POST /api/v1/ontology/ground` + Path Security + Semaphore(4)
# ---------------------------------------------------------------------------


def test_rest_ground_asset_path_security_and_pre_read_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _make_service(tmp_path, monkeypatch)
    app = create_app(service=svc)
    client = TestClient(app)

    outside_file = tmp_path / "outside_pack.json"
    outside_file.write_bytes(_jcs_bytes(_minimal_pack_dict()))
    ws_pack = svc.workspace_root / "custom_pack.json"
    ws_pack.write_bytes(_jcs_bytes(_minimal_pack_dict()))

    captured: dict[str, Any] = {}
    orig_ground = svc.ground_ontology

    async def _spy_ground(
        req: OntologyGroundReq,
        *,
        fibo_pack_bytes: bytes | None = None,
        glossary_bytes: bytes | None = None,
    ) -> GroundingResponse:
        captured["fibo_pack_bytes"] = fibo_pack_bytes
        captured["glossary_bytes"] = glossary_bytes
        return await orig_ground(
            req, fibo_pack_bytes=fibo_pack_bytes, glossary_bytes=glossary_bytes
        )

    monkeypatch.setattr(svc, "ground_ontology", _spy_ground)
    ok_resp = client.post(
        "/api/v1/ontology/ground",
        json={
            "request": _sample_ground_req().model_dump(mode="json"),
            "fibo_pack": str(ws_pack),
        },
    )
    assert ok_resp.status_code == 200, ok_resp.text
    assert isinstance(captured["fibo_pack_bytes"], bytes)
    assert len(captured["fibo_pack_bytes"]) == len(ws_pack.read_bytes())

    # Remote Host header with non-null asset path -> 403 LOOPBACK_ORIGIN_FORBIDDEN
    remote_resp = client.post(
        "/api/v1/ontology/ground",
        headers={"Host": "example.com"},
        json={
            "request": _sample_ground_req().model_dump(mode="json"),
            "fibo_pack": str(ws_pack),
        },
    )
    assert remote_resp.status_code == 403
    assert remote_resp.json()["error_code"] == "LOOPBACK_ORIGIN_FORBIDDEN"

    # Loopback outside workspace/packs root -> 403 LOOPBACK_ORIGIN_FORBIDDEN
    outside_resp = client.post(
        "/api/v1/ontology/ground",
        json={
            "request": _sample_ground_req().model_dump(mode="json"),
            "fibo_pack": str(outside_file),
        },
    )
    assert outside_resp.status_code == 403
    assert outside_resp.json()["error_code"] == "LOOPBACK_ORIGIN_FORBIDDEN"

    # Missing file inside workspace -> 404 RESOURCE_NOT_FOUND
    missing_resp = client.post(
        "/api/v1/ontology/ground",
        json={
            "request": _sample_ground_req().model_dump(mode="json"),
            "knowledge_source": "glossary",
            "glossary_path": str(svc.workspace_root / "nope.yaml"),
        },
    )
    assert missing_resp.status_code == 404, missing_resp.text
    assert missing_resp.json()["error_code"] == "RESOURCE_NOT_FOUND"

    # Syntax-forbidden paths (UNC, ADS, drive-relative, .., Windows reserved COM1, control char) -> 400
    for bad_path in (
        r"\\server\share\pack.json",
        str(ws_pack) + ":stream",
        "C:rel.json",
        "C:",
        "../outside_pack.json",
        "COM1.json",
        "bad\x07.json",
    ):
        r = client.post(
            "/api/v1/ontology/ground",
            json={
                "request": _sample_ground_req().model_dump(mode="json"),
                "fibo_pack": bad_path,
            },
        )
        assert r.status_code == 400, (bad_path, r.text)
        assert r.json()["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    # Symlink INSIDE workspace root -> 400 SYNTAX_INVALID_ARGUMENT (monkeypatched for non-elevated Windows)
    orig_reparse = zip_guard._is_reparse_or_symlink
    monkeypatch.setattr(
        zip_guard,
        "_is_reparse_or_symlink",
        lambda p: os.path.normcase(str(p)) == os.path.normcase(str(ws_pack)) or orig_reparse(p),
    )
    sym_inside = client.post(
        "/api/v1/ontology/ground",
        json={
            "request": _sample_ground_req().model_dump(mode="json"),
            "fibo_pack": str(ws_pack),
        },
    )
    assert sym_inside.status_code == 400
    assert sym_inside.json()["error_code"] == "SYNTAX_INVALID_ARGUMENT"
    monkeypatch.setattr(zip_guard, "_is_reparse_or_symlink", orig_reparse)

    # Oversized glossary (> 2 MB) -> 400 SYNTAX_INVALID_ARGUMENT
    big_glossary = svc.workspace_root / "big_glossary.yaml"
    big_glossary.write_bytes(b"a" * (2 * 1024 * 1024 + 10))
    big_resp = client.post(
        "/api/v1/ontology/ground",
        json={
            "request": _sample_ground_req().model_dump(mode="json"),
            "knowledge_source": "glossary",
            "glossary_path": str(big_glossary),
        },
    )
    assert big_resp.status_code == 400, big_resp.text
    assert big_resp.json()["error_code"] == "SYNTAX_INVALID_ARGUMENT"


def test_rest_ground_allows_symlink_above_workspace_root_r5_m1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5-M1: A symlinked parent directory ABOVE workspace_root must not fail containment."""
    alias_parent = tmp_path / "alias_root"
    real_parent = tmp_path / "real_root"
    ws_alias = alias_parent / "ws"
    ws_real = real_parent / "ws"
    ws_real.mkdir(parents=True, exist_ok=True)
    ws_alias.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_HOME", str(state_dir))

    pack_real = ws_real / "pack.json"
    pack_real.write_bytes(_jcs_bytes(_minimal_pack_dict()))

    svc = OpenDVService(ws_alias, state_dir=state_dir)
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOTS", str(ws_alias))
    app = create_app(service=svc)
    client = TestClient(app)

    orig_resolve = Path.resolve
    orig_realpath = os.path.realpath
    orig_reparse = zip_guard._is_reparse_or_symlink

    def _fake_resolve(self: Path, strict: bool = False) -> Path:
        s = os.path.normcase(str(self))
        if s.startswith(os.path.normcase(str(alias_parent))):
            rel = Path(str(self)[len(str(alias_parent)) :].lstrip("\\/"))
            return orig_resolve(real_parent / rel, strict=strict)
        return orig_resolve(self, strict=strict)

    def _fake_realpath(path: Any, *, strict: bool = False) -> str:
        s = str(path)
        if os.path.normcase(s).startswith(os.path.normcase(str(alias_parent))):
            rel = s[len(str(alias_parent)) :].lstrip("\\/")
            return str(orig_realpath(real_parent / rel, strict=strict))
        return orig_realpath(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", _fake_resolve)
    monkeypatch.setattr(os.path, "realpath", _fake_realpath)
    monkeypatch.setattr(
        zip_guard,
        "_is_reparse_or_symlink",
        lambda p: os.path.normcase(str(p)) == os.path.normcase(str(alias_parent)) or orig_reparse(p),
    )

    resp = client.post(
        "/api/v1/ontology/ground",
        json={
            "request": _sample_ground_req().model_dump(mode="json"),
            "fibo_pack": str(ws_alias / "pack.json"),
        },
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_rest_ground_default_env_raw_workspace_root_and_defensive_hardening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5-M1 default-env symlink-above-root via raw_workspace_root + inside-root 400 + hardening."""
    from opendv.models.grounding import (
        CandidateMapping,
        ColumnGroundingResult,
        ConfidenceInterval,
        InferredValueProfile,
        SemanticContext,
        TableGroundingResult,
    )
    from opendv.service._mcp_grounding_tool import _mint_receipts_for_response
    from opendv.service._mcp_tools import _derive_grounding_path

    alias_parent = tmp_path / "alias_default"
    real_parent = tmp_path / "real_default"
    ws_alias = alias_parent / "ws"
    ws_real = real_parent / "ws"
    (ws_real / "inside_real").mkdir(parents=True, exist_ok=True)
    (ws_alias / "inside_junc").mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "state_default"
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_HOME", str(state_dir))
    monkeypatch.delenv("OPENDV_WORKSPACE_ROOTS", raising=False)

    (ws_real / "pack.json").write_bytes(_jcs_bytes(_minimal_pack_dict()))
    (ws_real / "inside_real" / "pack.json").write_bytes(_jcs_bytes(_minimal_pack_dict()))

    orig_resolve = Path.resolve
    orig_realpath = os.path.realpath
    orig_reparse = zip_guard._is_reparse_or_symlink
    inside_junc = ws_alias / "inside_junc"

    def _fake_resolve(self: Path, strict: bool = False) -> Path:
        s = os.path.normcase(str(self))
        if s.startswith(os.path.normcase(str(inside_junc))):
            rel = Path(str(self)[len(str(inside_junc)) :].lstrip("\\/"))
            return orig_resolve(ws_real / "inside_real" / rel, strict=strict)
        if s.startswith(os.path.normcase(str(alias_parent))):
            rel = Path(str(self)[len(str(alias_parent)) :].lstrip("\\/"))
            return orig_resolve(real_parent / rel, strict=strict)
        return orig_resolve(self, strict=strict)

    def _fake_realpath(path: Any, *, strict: bool = False) -> str:
        s = str(path)
        if os.path.normcase(s).startswith(os.path.normcase(str(inside_junc))):
            rel = s[len(str(inside_junc)) :].lstrip("\\/")
            return str(orig_realpath(ws_real / "inside_real" / rel, strict=strict))
        if os.path.normcase(s).startswith(os.path.normcase(str(alias_parent))):
            rel = s[len(str(alias_parent)) :].lstrip("\\/")
            return str(orig_realpath(real_parent / rel, strict=strict))
        return orig_realpath(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", _fake_resolve)
    monkeypatch.setattr(os.path, "realpath", _fake_realpath)
    monkeypatch.setattr(
        zip_guard,
        "_is_reparse_or_symlink",
        lambda p: (
            os.path.normcase(str(p))
            in {os.path.normcase(str(alias_parent)), os.path.normcase(str(inside_junc))}
            or orig_reparse(p)
        ),
    )

    svc = OpenDVService(ws_alias, state_dir=state_dir)
    assert svc.raw_workspace_root == ws_alias
    assert svc.workspace_root == orig_resolve(ws_real)
    client = TestClient(create_app(service=svc))

    ok_above = client.post(
        "/api/v1/ontology/ground",
        json={
            "request": _sample_ground_req().model_dump(mode="json"),
            "fibo_pack": str(ws_alias / "pack.json"),
        },
    )
    assert ok_above.status_code == 200, ok_above.text

    bad_inside = client.post(
        "/api/v1/ontology/ground",
        json={
            "request": _sample_ground_req().model_dump(mode="json"),
            "fibo_pack": str(inside_junc / "pack.json"),
        },
    )
    assert bad_inside.status_code == 400, bad_inside.text
    assert bad_inside.json()["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    # Hardening 1: _mint_receipts_for_response must not mint receipts on AMBIGUOUS/unverified_masked_values
    ctx, _ = _make_broker_ctx(num_tables=1, cols_per_table=1)
    ambig_resp = GroundingResponse(
        knowledge_source_id="fibo-core",
        knowledge_source_version="2.0.0",
        knowledge_source_sha256="a" * 64,
        engine_version="2.0",
        mode_used="symbolic_only",
        tables=[
            TableGroundingResult(
                table_id="tbl_0",
                table_name="holdings",
                columns=[
                    ColumnGroundingResult(
                        table_name="holdings",
                        column_name="cusip",
                        status="AMBIGUOUS",
                        review_reason="unverified_masked_values",
                        no_match_probability=0.1,
                        steward_summary="Masked values require review",
                        execution_mode_used="symbolic_only",
                        selection_rule_applied="calibrator_top1",
                        inferred_value_profile=InferredValueProfile(
                            observed_sample_count=10,
                            non_null_count=10,
                            masked_count=10,
                            declared_logical_type="UTF8_STRING",
                            inferred_logical_type="UTF8_STRING",
                            type_confidence=1.0,
                            shape=ColumnShapeStats(profiled_non_null_count=0, distinct_ratio=0.0),
                        ),
                        choices=[
                            CandidateMapping(
                                rank=1,
                                entry_id=f"{_FIBO_BASE}/SEC/Debt/Bonds/Bond",
                                label="Bond",
                                entry_kind="class",
                                origin_kind="reference_standard",
                                suggested_predicate="skos:exactMatch",
                                confidence=ConfidenceInterval(
                                    point=0.82,
                                    lower=0.40,
                                    upper=0.96,
                                    level=0.90,
                                    calibration_mode="analytic_logit_normal",
                                ),
                                semantic_context=SemanticContext(archetype="entity"),
                            )
                        ],
                    )
                ],
            )
        ],
    )
    minted_resp, minted_rcpts = _mint_receipts_for_response(
        ctx,
        ambig_resp,
        table_id_map={"tbl_0": "tbl_0"},
        table_fp_map={"tbl_0": "fp-0-" + ("a" * 56)},
    )
    assert minted_resp.tables[0].columns[0].grounding_receipt_id is None
    assert minted_rcpts == {}

    # Hardening 2: _derive_grounding_path prefers PATH_A1 over PATH_A2 regardless of mapping order
    op_mixed = AddConcept(
        concept_uri="urn:opendv:am:Mixed",
        concept_name="Mixed",
        description="Mixed mappings",
        key_authority_tier="TIER_1_GLOBAL_STANDARD",
        external_mappings=[
            ExternalMapping(
                standard_uri=f"{_FIBO_BASE}/FBC/Accounts/Account",
                reference_pack_id="fibo-core",
                reference_pack_sha256="a" * 64,
                mapping_type="skos:closeMatch",
                confidence=Decimal("0.7000"),
            ),
            ExternalMapping(
                standard_uri=f"{_FIBO_BASE}/SEC/Debt/Bonds/Bond",
                reference_pack_id="fibo-core",
                reference_pack_sha256="a" * 64,
                mapping_type="skos:exactMatch",
                confidence=Decimal("0.9000"),
            ),
        ],
    )
    assert _derive_grounding_path(op_mixed) == "PATH_A1"

    # Hardening 3: DiscoveryService.ground_ontology tolerates source records with profile=None
    monkeypatch.setattr(Path, "resolve", orig_resolve)
    monkeypatch.setattr(os.path, "realpath", orig_realpath)
    monkeypatch.setattr(zip_guard, "_is_reparse_or_symlink", orig_reparse)
    svc2 = _make_service(tmp_path / "doc_none", monkeypatch)
    p2 = svc2.init_project(ProjectInitReq(project_id="doc-none", domain_namespace="am"))
    csv_f = svc2.workspace_root / "t.csv"
    csv_f.write_text("cusip\n037833100\n", encoding="utf-8")
    svc2.attach_source(p2.project_id, SourceAttachReq(kind="csv", uri_or_filename="t.csv"))
    doc_f = svc2.workspace_root / "notes.md"
    doc_f.write_text("# Domain Notes\nBond identifiers.\n", encoding="utf-8")
    doc_sum = svc2.attach_source(
        p2.project_id, SourceAttachReq(kind="markdown", uri_or_filename="notes.md")
    )
    orig_get_rec = svc2.state_manager.get_source_record

    def _patched_get_rec(pid: str, sid: str) -> dict[str, Any]:
        rec = orig_get_rec(pid, sid)
        if sid == doc_sum.source_id:
            return {**rec, "profile": None}
        return rec

    monkeypatch.setattr(svc2.state_manager, "get_source_record", _patched_get_rec)
    doc_none_resp = await svc2.ground_ontology(
        OntologyGroundReq(
            project_id=p2.project_id,
            policy=GroundingPolicy(mode="deterministic_only", skip_reason="profile_none_guard"),
        )
    )
    assert len(doc_none_resp.tables) == 1


@pytest.mark.asyncio
async def test_rest_ground_caps_tables_columns_and_semaphore_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _make_service(tmp_path, monkeypatch)
    monkeypatch.chdir(svc.workspace_root)
    proj = svc.init_project(
        ProjectInitReq(project_id="cap-proj", domain_namespace="asset_management")
    )
    tables = [_sample_table_profile(f"t_{i}", f"table_{i}", ["cusip"]) for i in range(51)]
    state_mgr = ProjectStateManager(svc.workspace_root)
    state_mgr.upsert_source(
        proj.project_id,
        source_id="src_many",
        kind="csv",
        uri_or_filename="mem://many.csv",
        binary_sha256="b" * 64,
        connector_config=ConnectorConfigPayload(
            adapter_id="csv",
            display_name="csv",
            uri_or_path="mem://many.csv",
            options={},
        ),
        status="READY_OFFLINE",
        profile=SourceProfile(
            source_id="src_many",
            schema_fingerprint="c" * 64,
            table_count=51,
            tables=tables,
        ),
        document_map=None,
        tables_index=[],
    )
    with pytest.raises(OpenDVError) as exc_info:
        await svc.ground_ontology(OntologyGroundReq(project_id=proj.project_id))
    assert exc_info.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    app = create_app(service=svc)
    active = 0
    max_active = 0

    async def _slow_ground(
        req: OntologyGroundReq,
        *,
        fibo_pack_bytes: bytes | None = None,
        glossary_bytes: bytes | None = None,
    ) -> GroundingResponse:
        nonlocal active, max_active
        _ = (req, fibo_pack_bytes, glossary_bytes)
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        return GroundingResponse(
            knowledge_source_id="fibo",
            knowledge_source_version="2.0.0",
            knowledge_source_sha256="0" * 64,
            engine_version="grounding-v2",
            mode_used="deterministic_only",
            tables=[],
        )

    monkeypatch.setattr(svc, "ground_ontology", _slow_ground)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as ac:
        body = {"request": _sample_ground_req().model_dump(mode="json")}
        responses = await asyncio.gather(
            *[ac.post("/api/v1/ontology/ground", json=body) for _ in range(8)]
        )
    assert all(r.status_code == 200 for r in responses), [
        (r.status_code, r.text) for r in responses
    ]
    assert max_active <= 4


# ---------------------------------------------------------------------------
# 3. Step-4 MCP `ground_columns` + Scout Masking + Coordinator Receipts
# ---------------------------------------------------------------------------


def test_mcp_ground_columns_receipts_offset_cascade_and_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, store = _make_broker_ctx(num_tables=2, cols_per_table=3, with_shape=True)
    res = coord_ground_columns(
        ctx,
        table_ids=["tbl_0"],
        steward_notes={"tbl_0.cusip": "North American CUSIP security identifier"},
        max_choices=3,
    )
    assert len(res["tables"]) == 1
    tbl = res["tables"][0]
    assert tbl["table_id"] == "tbl_0"
    assert tbl["columns"][0]["execution_mode_used"] in {"symbolic_fast_path", "symbolic_only"}
    receipt_id = tbl["columns"][0]["grounding_receipt_id"]
    assert receipt_id

    # Receipts are minted server-side in events_and_receipts with provenance="server_verified"
    receipts = store.list_events_or_receipts("p-gnd", record_kind="grounding_receipt")
    assert len(receipts) >= 1
    assert all(r["payload"]["provenance"] == "server_verified" for r in receipts)
    matching = [r["payload"] for r in receipts if r["payload"]["receipt_id"] == receipt_id]
    assert len(matching) == 1

    # Propose ontology delta referencing minted receipt + verify grounding_path derived
    delta_resp = coord_propose_ontology_delta(
        ctx,
        [
            ClientAdditiveOp(
                op=AddConcept(
                    concept_uri="urn:opendv:asset_management:bond_holding",
                    concept_name="Bond Holding",
                    description="Holding mapped via FIBO ground_columns",
                    key_authority_tier="TIER_2_ENTERPRISE_SOR",
                    external_mappings=[
                        ExternalMapping(
                            standard_uri=matching[0]["standard_uri"],
                            reference_pack_id=matching[0]["reference_pack_id"],
                            reference_pack_sha256=matching[0]["reference_pack_sha256"],
                            mapping_type=matching[0]["mapping_type"],
                            confidence=Decimal(str(matching[0]["confidence"])),
                            grounding_receipt_id=receipt_id,
                        )
                    ],
                    table_bindings=[
                        ConceptTableBinding(
                            source_id="src_0",
                            table_id="tbl_0",
                            col_name="cusip",
                        )
                    ],
                    attributes=[
                        ConceptAttribute(
                            name="cusip",
                            logical_type="UTF8_STRING",
                            sql_type_hint="VARCHAR",
                            nullable=False,
                            is_business_key=True,
                            is_pii=False,
                        )
                    ],
                )
            )
        ],
    )
    assert delta_resp.staged_count == 1
    staged_revs = store.list_revisions("p-gnd", rev_kind="staged_candidate")
    assert staged_revs[-1]["payload"]["grounding_path"] in {
        "PATH_A1",
        "PATH_A2",
        "PATH_B1",
        "PATH_B2",
    }

    # column_offset > 0 on multi-table scope -> SYNTAX_INVALID_ARGUMENT
    with pytest.raises(OpenDVError) as exc_multi:
        coord_ground_columns(ctx, table_ids=None, column_offset=1)
    assert exc_multi.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # column_offset > 0 on single table slices cleanly while still grounding all columns
    sliced = coord_ground_columns(ctx, table_ids=["tbl_0"], column_offset=1)
    assert [c["column_name"] for c in sliced["tables"][0]["columns"]] == [
        "maturity_date",
        "account_id",
    ]

    # 5-stage <= 64 KB cascade (test with tighter max_tool_payload_bytes to exercise all stages)
    tight_budget = HarnessBudget(
        max_turns=24,
        max_tokens=300_000,
        timeout_seconds=600,
        max_broker_calls=64,
        max_tool_payload_bytes=2200,
    )
    ctx_tight, _ = _make_broker_ctx(num_tables=2, cols_per_table=8, budget=tight_budget)
    monkeypatch.setattr(
        "opendv.service._mcp_grounding_tool._MAX_MCP_GROUND_BYTES",
        2200,
    )
    cascaded = coord_ground_columns(ctx_tight)
    encoded_bytes = len(json.dumps(cascaded, default=str, separators=(",", ":")).encode("utf-8"))
    assert encoded_bytes <= 2200
    assert len(cascaded["tables"]) == 1
    assert cascaded["truncated_table_ids"] == ["tbl_1"]
    assert cascaded["tables"][0]["next_column_offset"] == len(cascaded["tables"][0]["columns"])

    # Exceeding budget.max_broker_calls -> UNCERTIFIED_DRAFT
    exhausted_ctx, _ = _make_broker_ctx(
        num_tables=1,
        cols_per_table=2,
        budget=HarnessBudget(
            max_turns=24, max_tokens=300_000, timeout_seconds=600, max_broker_calls=1
        ),
    )
    coord_ground_columns(exhausted_ctx, table_ids=["tbl_0"])
    with pytest.raises(OpenDVError) as exc_budget:
        coord_ground_columns(exhausted_ctx, table_ids=["tbl_0"])
    assert exc_budget.value.error_code == ErrorCode.UNCERTIFIED_DRAFT


def test_scout_inspect_table_profile_strips_shape_stats() -> None:
    ctx, _ = _make_broker_ctx(num_tables=1, cols_per_table=3, with_shape=True)
    prof = scout_inspect_table_profile(ctx, "tbl_0")
    assert all(col.shape_stats is None for col in prof.columns)


def test_subprocess_cli_canary_offline_grounding_json(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    req_path = ws / "req.json"
    req_path.write_text(_sample_ground_req().model_dump_json(), encoding="utf-8")
    env = {
        **os.environ,
        "OPENDV_HOME": str(tmp_path / "state"),
        "OPENDV_WORKSPACE_ROOTS": str(ws),
    }
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from opendv.cli.main import main; raise SystemExit(main())",
            "--json",
            "ontology",
            "ground",
            "--input",
            str(req_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ws),
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["data"]["mode_used"] == "deterministic_only"
