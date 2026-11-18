"""Integration and unit tests for Universal Source Adapter metadata and introspection REST APIs."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from opendv_contracts.dtos.sources import (
    ConnectorUIManifestDTO,
    SourceInspectRequestDTO,
    SourceInspectResponseDTO,
)
from opendv_platform_api import PlatformStateContainer, create_app


@pytest.fixture
def api_client(tmp_path: Path) -> tuple[TestClient, PlatformStateContainer]:
    """Create test client with clean isolated state container."""
    state = PlatformStateContainer(
        state_dir=tmp_path / "state",
        workspace_root=tmp_path,
    )
    app = create_app(platform_state=state, workspace_root=tmp_path)
    client = TestClient(app, headers={"X-OpenDV-Role": "DATA_STEWARD"})
    return client, state


@pytest.fixture
def sqlite_fixture_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite database with PK and FK constraints."""
    db_path = tmp_path / "ecommerce.sqlite"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            email TEXT NOT NULL,
            full_name TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            order_total REAL NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        )
        """
    )
    cur.execute("INSERT INTO customers VALUES (1, 'alice@example.com', 'Alice Smith')")
    cur.execute("INSERT INTO orders VALUES (101, 1, 99.50)")
    conn.commit()
    conn.close()
    return db_path


def test_get_sources_adapters_manifests(
    api_client: tuple[TestClient, PlatformStateContainer],
) -> None:
    """Verify GET /api/v1/sources/adapters returns 200 with list of connector UI manifests."""
    client, _ = api_client
    resp = client.get("/api/v1/sources/adapters")
    assert resp.status_code == 200, resp.text
    manifests_raw = resp.json()
    assert isinstance(manifests_raw, list)
    assert len(manifests_raw) >= 4

    manifests = [ConnectorUIManifestDTO.model_validate(m) for m in manifests_raw]
    adapter_ids = {m.adapter_id for m in manifests}
    assert {"sqlite", "databricks", "duckdb", "file"}.issubset(adapter_ids)

    # SQLite properties
    sqlite_m = next(m for m in manifests if m.adapter_id == "sqlite")
    assert sqlite_m.client_uploadable is True
    assert sqlite_m.supports_foreign_keys is True
    assert sqlite_m.execution_mode == "DIRECT_DUCKDB_ATTACH"

    # Databricks properties
    dbx_m = next(m for m in manifests if m.adapter_id == "databricks")
    assert dbx_m.client_uploadable is False
    assert dbx_m.supports_catalog_hierarchy is True
    assert dbx_m.execution_mode == "REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE"

    # File and DuckDB properties
    duck_m = next(m for m in manifests if m.adapter_id == "duckdb")
    assert duck_m.client_uploadable is True
    assert duck_m.execution_mode == "DIRECT_DUCKDB_ATTACH"

    file_m = next(m for m in manifests if m.adapter_id == "file")
    assert file_m.client_uploadable is True


def test_post_sources_inspect_sqlite_with_pk_fk(
    api_client: tuple[TestClient, PlatformStateContainer],
    sqlite_fixture_db: Path,
) -> None:
    """Verify POST /api/v1/sources/inspect on SQLite extracts tables, columns, PK, and FK."""
    client, _ = api_client
    req = SourceInspectRequestDTO(
        adapter="sqlite",
        uri=f"sqlite:///{sqlite_fixture_db.as_posix()}",
    )
    resp = client.post("/api/v1/sources/inspect", json=req.model_dump(mode="json"))
    assert resp.status_code == 200, resp.text

    inspect_data = SourceInspectResponseDTO.model_validate(resp.json())
    assert inspect_data.adapter_id == "sqlite"
    assert len(inspect_data.tables) == 2

    table_map = {t["table_name"]: t for t in inspect_data.tables}
    assert "customers" in table_map
    assert "orders" in table_map

    # Check customers table
    cust_tbl = table_map["customers"]
    assert cust_tbl["primary_key_columns"] == ["customer_id"]
    cust_col_names = [c["name"] for c in cust_tbl["columns"]]
    assert "customer_id" in cust_col_names
    assert "email" in cust_col_names

    # Check orders table foreign keys
    orders_tbl = table_map["orders"]
    assert orders_tbl["primary_key_columns"] == ["order_id"]
    assert len(orders_tbl["foreign_keys"]) >= 1
    fk = orders_tbl["foreign_keys"][0]
    assert fk["target_table"] == "customers"
    assert fk["target_column"] == "customer_id"

    # Also verify column level foreign_key_ref
    order_cust_col = next(c for c in orders_tbl["columns"] if c["name"] == "customer_id")
    assert order_cust_col["foreign_key_ref"] is not None
    assert order_cust_col["foreign_key_ref"]["target_table"] == "customers"

    # Verify deterministic fingerprint
    assert inspect_data.fingerprint is not None
    assert len(inspect_data.fingerprint) == 64


def test_post_sources_inspect_security_enforcement(
    api_client: tuple[TestClient, PlatformStateContainer],
    sqlite_fixture_db: Path,
) -> None:
    """Verify POST /api/v1/sources/inspect fails closed on invalid credentials_ref or secrets."""
    client, _ = api_client

    # 1. Invalid credentials_ref pattern
    req_bad_cred = {
        "adapter": "sqlite",
        "uri": f"sqlite:///{sqlite_fixture_db.as_posix()}",
        "credentials_ref": "plaintext_super_secret_token",
    }
    resp_bad_cred = client.post("/api/v1/sources/inspect", json=req_bad_cred)
    assert resp_bad_cred.status_code in {400, 422}
    assert "credentials_ref" in resp_bad_cred.text

    # 2. Literal secret in options
    for forbidden_key in ["password", "token", "api_key", "secret"]:
        req_secret_opt = {
            "adapter": "sqlite",
            "uri": f"sqlite:///{sqlite_fixture_db.as_posix()}",
            "options": {forbidden_key: "plain_secret_123"},
        }
        resp_secret = client.post("/api/v1/sources/inspect", json=req_secret_opt)
        assert resp_secret.status_code == 400
        assert "literal credentials" in resp_secret.text or forbidden_key in resp_secret.text


def test_post_sources_inspect_cas_staged_upload(
    api_client: tuple[TestClient, PlatformStateContainer],
    sqlite_fixture_db: Path,
) -> None:
    """Verify POST /api/v1/sources/inspect resolves CAS binary hash uploaded to store."""
    client, state = api_client

    # Upload sqlite database bytes into CAS
    db_bytes = sqlite_fixture_db.read_bytes()
    expected_hash = hashlib.sha256(db_bytes).hexdigest().lower()
    state.cas_store.put(db_bytes)

    # Inspect via cas_hash
    req = {
        "uri": f"cas://{expected_hash}",
        "adapter": "sqlite",
        "cas_hash": expected_hash,
    }
    resp = client.post("/api/v1/sources/inspect", json=req)
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert data["adapter_id"] == "sqlite"
    assert len(data["tables"]) == 2

    # Negative check: non-existent cas_hash returns 404
    missing_hash = "0" * 64
    resp_missing = client.post(
        "/api/v1/sources/inspect",
        json={"uri": f"cas://{missing_hash}", "adapter": "sqlite", "cas_hash": missing_hash},
    )
    assert resp_missing.status_code in {400, 404}


def test_post_cas_blobs_lifecycle_and_inspect(
    api_client: tuple[TestClient, PlatformStateContainer],
    sqlite_fixture_db: Path,
) -> None:
    """Verify authenticated POST /api/v1/cas/blobs lifecycle and subsequent source inspection."""
    client, _ = api_client
    db_bytes = sqlite_fixture_db.read_bytes()
    expected_hash = hashlib.sha256(db_bytes).hexdigest().lower()

    # 1. Digest mismatch rejected
    bad_hash = "a" * 64
    resp_bad = client.post(
        "/api/v1/cas/blobs",
        content=db_bytes,
        headers={"Content-Type": "application/octet-stream", "X-CAS-SHA256": bad_hash},
    )
    assert resp_bad.status_code == 400
    assert "digest mismatch" in resp_bad.text

    # 2. Invalid header format rejected
    resp_invalid_hdr = client.post(
        "/api/v1/cas/blobs",
        content=db_bytes,
        headers={"Content-Type": "application/octet-stream", "X-CAS-SHA256": "not-a-valid-hex"},
    )
    assert resp_invalid_hdr.status_code == 400

    # 3. Successful upload returns 201 Created
    resp_upload = client.post(
        "/api/v1/cas/blobs",
        content=db_bytes,
        headers={"Content-Type": "application/octet-stream", "X-CAS-SHA256": expected_hash},
    )
    assert resp_upload.status_code == 201, resp_upload.text
    body = resp_upload.json()
    assert body["sha256"] == expected_hash
    assert body["size_bytes"] == len(db_bytes)
    assert body["uri"] == f"cas://{expected_hash}"

    # 4. Fetch blob via GET /api/v1/cas/blobs/{sha256}
    resp_get = client.get(f"/api/v1/cas/blobs/{expected_hash}")
    assert resp_get.status_code == 200
    assert resp_get.content == db_bytes

    # 5. Inspect the uploaded database via /api/v1/sources/inspect
    resp_inspect = client.post(
        "/api/v1/sources/inspect",
        json={"uri": f"cas://{expected_hash}"},
    )
    assert resp_inspect.status_code == 200, resp_inspect.text
    inspect_data = resp_inspect.json()
    assert inspect_data["adapter_id"] == "sqlite"
    assert len(inspect_data["tables"]) == 2


def test_post_sources_inspect_and_cas_unauthenticated_rejected(
    api_client: tuple[TestClient, PlatformStateContainer],
    sqlite_fixture_db: Path,
) -> None:
    """Verify that unauthenticated PUBLIC role cannot inspect sources or upload CAS blobs.

    Covers R2-C1 and R2-C3.
    """
    client, _ = api_client
    resp_inspect = client.post(
        "/api/v1/sources/inspect",
        json={"adapter": "sqlite", "uri": f"sqlite:///{sqlite_fixture_db.as_posix()}"},
        headers={"X-OpenDV-Role": "PUBLIC"},
    )
    assert resp_inspect.status_code == 403

    resp_cas = client.post(
        "/api/v1/cas/blobs",
        content=b"unauth_test",
        headers={"X-OpenDV-Role": "PUBLIC", "X-CAS-SHA256": "0" * 64},
    )
    assert resp_cas.status_code == 403


