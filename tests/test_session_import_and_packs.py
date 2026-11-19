"""Phase 2 Track B TDD Tests: Session Creation, Baseline Packs, TTL/YAML/JSON Import, and D25 Filtering.

Covers:
- FR-ING-01: Blank v0.0.0, Workspace Seed, 6 Asset-Management Starter Packs,
  and POST /api/v1/ontology/studio/sessions/import-ontology (YAML/JSON/TTL w/ rdflib & JCS SHA-256).
- FR-SES-01 / D25: wizard_schema_version=2 on new sessions, GET /sessions?wizard_only=true
  filtering + legacy_session_count, and CLI/manager compatibility on legacy sessions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from starlette.testclient import TestClient

from opendv_contracts.dtos.starter_packs import AssetManagementPackId
from opendv_core.genesis import create_genesis_ontology
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    ConceptAttribute,
    ExternalMapping,
    GraphAOntology,
)
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings
from opendv_service.export.rdf_exporter import export_ontology
from opendv_service.studio.session_manager import SessionMetadata, StudioSessionManager


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from opendv_core.schema_registry import get_default_schema_registry

    get_default_schema_registry()
    studio_root = tmp_path / "opendv_studio"
    studio_root.mkdir(parents=True, exist_ok=True)
    ws_root = tmp_path / "workspace"
    ws_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
    return studio_root


def _client(tmp_path: Path) -> TestClient:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    settings = OpenDVSettings.load(workspace_root=ws, state_dir=tmp_path / "state", is_server=True)
    app = create_app(settings=settings, workspace_root=ws, storage_root=tmp_path / "state")
    return TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")


def _sample_ontology(namespace: str = "custody_ops") -> GraphAOntology:
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace=namespace,
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:instrument",
                concept_name="Instrument",
                elm_archetype="CORE_CONCEPT",
                target_hub="hub_instrument",
                definition="Tradable financial instrument.",
                attributes=[
                    ConceptAttribute(attribute_name="isin_code", data_type="string", is_optional=False)
                ],
                external_mappings=[
                    ExternalMapping(
                        scheme="fibo",
                        target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/FinancialInstruments/FinancialInstrument",
                        predicate="skos:exactMatch",
                        confidence=0.95,
                    )
                ],
            )
        ],
    )


def test_blank_session_v0_genesis_and_wizard_schema_version(
    tmp_path: Path, isolated_env: Path
) -> None:
    """New blank sessions initialize v0.0.0 genesis with JCS SHA-256 and wizard_schema_version=2."""
    client = _client(tmp_path)
    resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={
            "session_id": "blank-v2-session",
            "domain_namespace": "custody_ops",
            "enterprise_owner": "Custody Team",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["active_version"] == "0.0.0"

    expected_genesis_sha = compute_ontology_sha256(
        create_genesis_ontology("custody_ops", enterprise_owner="Custody Team")
    )
    assert body["head_sha256"] == expected_genesis_sha

    session_json_path = isolated_env / "blank-v2-session" / "session.json"
    raw_meta = json.loads(session_json_path.read_text(encoding="utf-8"))
    assert raw_meta.get("wizard_schema_version") == 2
    assert SessionMetadata.model_validate(raw_meta).wizard_schema_version == 2


def test_workspace_seed_session_creation(tmp_path: Path, isolated_env: Path) -> None:
    """Creating a session with from_workspace seeds concepts and stamps wizard_schema_version=2."""
    ws_seed = tmp_path / "workspace" / "seed_ont.yaml"
    ont = _sample_ontology("workspace_seed")
    ws_seed.write_text(yaml.safe_dump(ont.model_dump(mode="json", exclude_none=True)), encoding="utf-8")

    client = _client(tmp_path)
    resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={
            "session_id": "seeded-v2-session",
            "domain_namespace": "workspace_seed",
            "from_workspace": "seed_ont.yaml",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["head_sha256"] == compute_ontology_sha256(ont)

    raw_meta = json.loads((isolated_env / "seeded-v2-session" / "session.json").read_text(encoding="utf-8"))
    assert raw_meta.get("wizard_schema_version") == 2


def test_six_asset_management_starter_packs_listing_and_wizard_helper(
    tmp_path: Path, isolated_env: Path
) -> None:
    """All 6 AM vertical packs + wizard starter_packs helper create wizard_schema_version=2 sessions."""
    from opendv_service.studio.starter_packs import (
        create_session_from_starter_pack,
        list_wizard_starter_packs,
    )

    client = _client(tmp_path)
    resp = client.get("/api/v1/ontology/studio/starter-packs")
    assert resp.status_code == 200, resp.text
    packs = resp.json()["packs"]
    pack_ids = {p["pack_id"] for p in packs}
    expected_six = {
        AssetManagementPackId.PUBLIC_EQUITIES_OMS.value,
        AssetManagementPackId.FIXED_INCOME_RATES.value,
        AssetManagementPackId.MULTI_ASSET_IBOR.value,
        AssetManagementPackId.PRIVATE_EQUITY_BUYOUT.value,
        AssetManagementPackId.PRIVATE_CREDIT_DIRECT_LENDING.value,
        AssetManagementPackId.CUSTODY_CLEARING.value,
    }
    assert expected_six.issubset(pack_ids)

    wizard_packs = list_wizard_starter_packs()
    assert len(wizard_packs) >= 6

    mgr = StudioSessionManager(studio_root=isolated_env, workspace_root=tmp_path / "workspace")
    created_meta = create_session_from_starter_pack(
        mgr,
        session_id="pack-equities-01",
        pack_id=AssetManagementPackId.PUBLIC_EQUITIES_OMS.value,
    )
    assert created_meta.wizard_schema_version == 2
    assert created_meta.head_version != "0.0.0"


@pytest.mark.parametrize("fmt", ["yaml", "json", "ttl"])
def test_import_ontology_yaml_json_ttl_and_jcs_sha256(
    tmp_path: Path, isolated_env: Path, fmt: str
) -> None:
    """POST /sessions/import-ontology imports YAML/JSON/TTL via rdflib and verifies JCS SHA-256."""
    from opendv_service.studio.ttl_importer import parse_ontology_import_payload

    ont = _sample_ontology("imported_ns")
    expected_sha = compute_ontology_sha256(ont)
    content, _ = export_ontology(ont, fmt)

    parsed = parse_ontology_import_payload(content, import_format=fmt, domain_namespace="imported_ns")
    assert len(parsed.canonical_business_concepts) == 1
    assert parsed.canonical_business_concepts[0].concept_uri == "urn:ontology:entity:instrument"
    assert compute_ontology_sha256(parsed) == expected_sha

    client = _client(tmp_path)
    resp = client.post(
        "/api/v1/ontology/studio/sessions/import-ontology",
        json={
            "session_id": f"imp-{fmt}-01",
            "domain_namespace": "imported_ns",
            "format": fmt,
            "content": content,
            "expected_sha256": expected_sha,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["head_sha256"] == expected_sha
    meta = json.loads((isolated_env / f"imp-{fmt}-01" / "session.json").read_text(encoding="utf-8"))
    assert meta.get("wizard_schema_version") == 2


def test_import_ontology_rejects_malformed_ttl_or_sha_mismatch(tmp_path: Path) -> None:
    """Malformed Turtle or JCS SHA-256 mismatch fails closed with 400/422."""
    client = _client(tmp_path)
    bad_ttl = client.post(
        "/api/v1/ontology/studio/sessions/import-ontology",
        json={
            "session_id": "bad-ttl-01",
            "domain_namespace": "imported_ns",
            "format": "ttl",
            "content": "@prefix broken: <<<not valid turtle",
        },
    )
    assert bad_ttl.status_code in (400, 422), bad_ttl.text

    ont = _sample_ontology("imported_ns")
    yaml_content, _ = export_ontology(ont, "yaml")
    bad_sha = client.post(
        "/api/v1/ontology/studio/sessions/import-ontology",
        json={
            "session_id": "bad-sha-01",
            "domain_namespace": "imported_ns",
            "format": "yaml",
            "content": yaml_content,
            "expected_sha256": "0" * 64,
        },
    )
    assert bad_sha.status_code in (400, 412, 422), bad_sha.text


def test_get_sessions_wizard_only_filtering_legacy_count_and_cli_compat(
    tmp_path: Path, isolated_env: Path
) -> None:
    """GET /sessions?wizard_only=true filters legacy sessions and returns legacy_session_count without mutating disk."""
    client = _client(tmp_path)
    # Create one v2 session via API
    r_new = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "wizard-v2-01", "domain_namespace": "custody_ops"},
    )
    assert r_new.status_code == 201

    # Seed a legacy v1 session directly on disk (no wizard_schema_version key)
    mgr = StudioSessionManager(studio_root=isolated_env, workspace_root=tmp_path / "workspace")
    legacy_meta = mgr.create_session(session_id="legacy-v1-01", domain_namespace="custody_ops")
    legacy_json_path = isolated_env / "legacy-v1-01" / "session.json"
    legacy_dict = json.loads(legacy_json_path.read_text(encoding="utf-8"))
    legacy_dict.pop("wizard_schema_version", None)
    legacy_before = json.dumps(legacy_dict, indent=2)
    legacy_json_path.write_text(legacy_before, encoding="utf-8")

    # Wizard filtered query
    wiz_resp = client.get("/api/v1/ontology/studio/sessions?wizard_only=true")
    assert wiz_resp.status_code == 200, wiz_resp.text
    wiz_body: Any = wiz_resp.json()
    assert isinstance(wiz_body, dict), f"Expected dict with sessions + legacy_session_count, got {type(wiz_body)}"
    assert wiz_body["legacy_session_count"] == 1
    returned_ids = {s["session_id"] for s in wiz_body["sessions"]}
    assert returned_ids == {"wizard-v2-01"}

    # Legacy file must NOT be silently mutated
    assert legacy_json_path.read_text(encoding="utf-8") == legacy_before

    # CLI / StudioSessionManager backward compatibility remains intact on legacy sessions
    all_sessions = {s.session_id for s in mgr.list_sessions()}
    assert "legacy-v1-01" in all_sessions
    assert mgr.get_session("legacy-v1-01").head_sha256 == legacy_meta.head_sha256
