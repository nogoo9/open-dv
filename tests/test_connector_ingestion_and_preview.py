"""Phase 2 Track B TDD Tests: Multi-Connector Ingestion, Multimodal Upload, Inline Notes & Evidence Previews.

Covers:
- FR-ING-02: POST /sessions/{id}/sources/connectors composing sources_inspect + GraphBProfiler
  across SQLite, DuckDB, CSV, TSV, Parquet, XLSX, JSONL with _FORBIDDEN_SECRET_KEYS & credentials_ref guards.
- FR-ING-03: Multimodal upload w/ OCR auto|force|off & do_not_send_to_agent; Inline Domain Notes
  with CRLF-normalized [char_start, char_end) spans.
- FR-PRV-01, FR-PRV-02, D6: GET /evidence/tables (<= 50 sample rows, PK/FK, entropy, FIBO gap hints)
  and GET /evidence/chunks (chunk_id, page_number, start_char, end_char, confidence, is_table, table_data).
"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from starlette.testclient import TestClient

from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings


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


def _create_session(client: TestClient, session_id: str = "ing-sess-01") -> None:
    resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "custody_ops"},
    )
    assert resp.status_code == 201, resp.text


def _build_sqlite_db(path: Path, row_count: int = 65) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(
            "CREATE TABLE counterparties (cp_id TEXT PRIMARY KEY, legal_name TEXT NOT NULL);"
        )
        conn.execute(
            "CREATE TABLE ledger_positions ("
            "row_seq_sk INTEGER PRIMARY KEY AUTOINCREMENT, "
            "pos_code TEXT NOT NULL, "
            "cp_id TEXT REFERENCES counterparties(cp_id), "
            "market_val REAL"
            ");"
        )
        conn.execute("INSERT INTO counterparties VALUES ('CP-1', 'Alpha Fund'), ('CP-2', 'Beta LP');")
        for idx in range(row_count):
            cp = "CP-1" if idx % 2 == 0 else None
            conn.execute(
                "INSERT INTO ledger_positions (pos_code, cp_id, market_val) VALUES (?, ?, ?)",
                (f"POS-{idx:04d}", cp, float(1000 + idx)),
            )
        conn.commit()
    finally:
        conn.close()


def _build_minimal_xlsx(path: Path) -> None:
    """Write a minimal valid OOXML .xlsx workbook with one sheet and two rows."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheets><sheet name="Trades" sheetId="1" r:id="rId1" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets>'
            "</workbook>",
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            '<row r="1"><c r="A1" t="inlineStr"><is><t>trade_id</t></is></c>'
            '<c r="B1" t="inlineStr"><is><t>notional</t></is></c></row>'
            '<row r="2"><c r="A2" t="inlineStr"><is><t>T100</t></is></c>'
            '<c r="B2"><v>25000</v></c></row>'
            "</sheetData></worksheet>",
        )


def test_connectors_across_sqlite_duckdb_csv_tsv_parquet_xlsx_jsonl(tmp_path: Path) -> None:
    """POST /sources/connectors composes sources_inspect + GraphBProfiler across all 7 tabular formats."""
    from opendv_service.studio.connector_ingestion import SUPPORTED_CONNECTOR_FORMATS

    assert {"sqlite", "duckdb", "csv", "tsv", "parquet", "xlsx", "jsonl"}.issubset(
        set(SUPPORTED_CONNECTOR_FORMATS)
    )

    ws = tmp_path / "workspace"
    sqlite_p = ws / "feed.sqlite"
    _build_sqlite_db(sqlite_p, row_count=10)

    ddb_p = ws / "analytics.duckdb"
    dconn = duckdb.connect(str(ddb_p))
    dconn.execute("CREATE TABLE settle_events (evt_id VARCHAR, qty DOUBLE);")
    dconn.execute("INSERT INTO settle_events VALUES ('E1', 10.5), ('E2', 20.0);")
    dconn.close()

    csv_p = ws / "orders.csv"
    csv_p.write_text("order_id,side,qty\nO1,BUY,100\nO2,SELL,50\n", encoding="utf-8")

    tsv_p = ws / "fills.tsv"
    tsv_p.write_text("fill_id\tprice\nF1\t10.25\nF2\t11.50\n", encoding="utf-8")

    pq_p = ws / "holdings.parquet"
    pq.write_table(pa.table({"acct_id": ["A1", "A2"], "bal": [100.0, 200.0]}), pq_p)

    xlsx_p = ws / "trades.xlsx"
    _build_minimal_xlsx(xlsx_p)

    jsonl_p = ws / "events.jsonl"
    jsonl_p.write_text('{"id":"J1","amt":12}\n{"id":"J2","amt":18}\n', encoding="utf-8")

    client = _client(tmp_path)
    _create_session(client, "conn-multi-01")

    for adapter, path in [
        ("sqlite", sqlite_p),
        ("duckdb", ddb_p),
        ("file", csv_p),
        ("file", tsv_p),
        ("file", pq_p),
        ("file", xlsx_p),
        ("file", jsonl_p),
    ]:
        resp = client.post(
            "/api/v1/ontology/studio/sessions/conn-multi-01/sources/connectors",
            json={"adapter": adapter, "uri": str(path)},
        )
        assert resp.status_code in (200, 201), f"{adapter} {path.name} failed: {resp.text}"
        body = resp.json()
        assert body["table_count"] >= 1


def test_connector_rejects_forbidden_secrets_and_invalid_credentials_ref(tmp_path: Path) -> None:
    """Connector ingestion rejects raw _FORBIDDEN_SECRET_KEYS and invalid credentials_ref patterns."""
    from opendv_core.config import validate_credentials_ref

    assert validate_credentials_ref("env:DB_TOKEN") == "env:DB_TOKEN"
    assert validate_credentials_ref("secret:vault_key-1") == "secret:vault_key-1"
    with pytest.raises(ValueError):
        validate_credentials_ref("raw-secret-literal")

    csv_p = tmp_path / "workspace" / "ok.csv"
    csv_p.write_text("id,val\n1,abc\n", encoding="utf-8")

    client = _client(tmp_path)
    _create_session(client, "conn-sec-01")

    for bad_payload in [
        {"adapter": "file", "uri": str(csv_p), "password": "plain-password"},
        {"adapter": "file", "uri": str(csv_p), "options": {"api_key": "dapi1234"}},
        {"adapter": "file", "uri": str(csv_p), "credentials_ref": "plain_token_not_ref"},
    ]:
        resp = client.post(
            "/api/v1/ontology/studio/sessions/conn-sec-01/sources/connectors",
            json=bad_payload,
        )
        assert resp.status_code in (400, 422), resp.text


def test_multimodal_upload_ocr_modes_and_do_not_send_to_agent(
    tmp_path: Path, isolated_env: Path
) -> None:
    """Upload supports ocr_mode (auto|force|off) and do_not_send_to_agent privacy flags."""
    client = _client(tmp_path)
    _create_session(client, "mm-sess-01")

    resp = client.post(
        "/api/v1/ontology/studio/sessions/mm-sess-01/sources",
        files={"file": ("restricted_memo.md", io.BytesIO(b"Private fee schedule: 45 bps."), "text/markdown")},
        data={"do_not_send_to_agent": "true", "ocr_mode": "off", "description": "Restricted memo"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["do_not_send_to_agent"] is True

    taint_file = isolated_env / "mm-sess-01" / "taint_index.json"
    assert taint_file.is_file()
    taint_data = json.loads(taint_file.read_text(encoding="utf-8"))
    assert taint_data["is_tainted"] is True

    manifest_data = json.loads((isolated_env / "mm-sess-01" / "inputs" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_data["files"][0].get("ocr_mode") == "off"


def test_inline_domain_notes_crlf_normalized_char_spans(tmp_path: Path) -> None:
    """Inline Domain Notes normalize CRLF -> LF so [start_char, end_char) matches chunk text."""
    client = _client(tmp_path)
    _create_session(client, "note-crlf-01")

    raw_crlf = "Settlement SLA Rule:\r\nEvery T+1 trade settles within 24 hours.\r\nPenalty 5 bps."
    normalized = raw_crlf.replace("\r\n", "\n")

    resp = client.post(
        "/api/v1/ontology/studio/sessions/note-crlf-01/sources/text",
        json={
            "text": raw_crlf,
            "source_name": "sla_note.md",
            "format": "markdown",
            "do_not_send_to_agent": False,
        },
    )
    assert resp.status_code == 201, resp.text

    parse_resp = client.post(
        "/api/v1/ontology/studio/sessions/note-crlf-01/parse",
        json={"ocr_mode": "off"},
    )
    assert parse_resp.status_code == 202, parse_resp.text
    _jid = parse_resp.json()["job_id"]
    import time as _t
    for _ in range(50):
        if client.get(f"/api/v1/ontology/studio/jobs/{_jid}").json().get("status") in ("completed", "failed"):
            break
        _t.sleep(0.05)

    chunks_resp = client.get("/api/v1/ontology/studio/sessions/note-crlf-01/evidence/chunks")
    assert chunks_resp.status_code == 200, chunks_resp.text
    chunks = chunks_resp.json()["chunks"]
    assert len(chunks) >= 1
    for c in chunks:
        assert "\r\n" not in c["text"]
        s, e = c["start_char"], c["end_char"]
        assert s is not None and e is not None
        assert normalized[s:e] == c["text"]


def test_evidence_tables_preview_caps_50_rows_with_stats_and_fibo_gaps(tmp_path: Path) -> None:
    """GET /evidence/tables returns <= 50 sample rows, SQL types, PK/FK, entropy, and FIBO gap hints."""
    sqlite_p = tmp_path / "workspace" / "big_positions.sqlite"
    _build_sqlite_db(sqlite_p, row_count=65)

    client = _client(tmp_path)
    _create_session(client, "prv-tbl-01")
    conn_resp = client.post(
        "/api/v1/ontology/studio/sessions/prv-tbl-01/sources/connectors",
        json={"adapter": "sqlite", "uri": str(sqlite_p)},
    )
    assert conn_resp.status_code in (200, 201), conn_resp.text

    resp = client.get("/api/v1/ontology/studio/sessions/prv-tbl-01/evidence/tables")
    assert resp.status_code == 200, resp.text
    tables = resp.json()["tables"]
    pos_tbl = next(t for t in tables if t["table_name"].endswith("ledger_positions"))
    assert len(pos_tbl["sample_rows"]) <= 50
    assert len(pos_tbl["sample_rows"]) == 50
    cols = {c["column_name"]: c for c in pos_tbl["columns"]}
    assert cols["row_seq_sk"]["is_primary_key"] is True
    assert cols["row_seq_sk"].get("surrogate_hint") is True
    assert cols["cp_id"].get("foreign_key") is not None
    assert "null_rate" in cols["cp_id"] and "shannon_entropy" in cols["pos_code"]
    assert "fibo_gap_hints" in pos_tbl


def test_evidence_chunks_preview_returns_required_fields_and_table_data(tmp_path: Path) -> None:
    """GET /evidence/chunks returns chunk_id, page_number, start_char, end_char, confidence, is_table, table_data."""
    client = _client(tmp_path)
    _create_session(client, "prv-chk-01")

    md_table = (
        "# Fee Schedule\n\n"
        "| Tier | Bps |\n"
        "| --- | --- |\n"
        "| Institutional | 15 |\n"
    )
    up = client.post(
        "/api/v1/ontology/studio/sessions/prv-chk-01/sources",
        files={"file": ("fees.md", io.BytesIO(md_table.encode("utf-8")), "text/markdown")},
        data={"do_not_send_to_agent": "false"},
    )
    assert up.status_code == 201
    pr2 = client.post("/api/v1/ontology/studio/sessions/prv-chk-01/parse", json={"ocr_mode": "off"})
    assert pr2.status_code == 202
    _jid2 = pr2.json()["job_id"]
    import time as _t
    for _ in range(50):
        if client.get(f"/api/v1/ontology/studio/jobs/{_jid2}").json().get("status") in ("completed", "failed"):
            break
        _t.sleep(0.05)

    resp = client.get("/api/v1/ontology/studio/sessions/prv-chk-01/evidence/chunks")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["chunks"]
    first = payload["chunks"][0]
    for required_key in ("chunk_id", "page_number", "start_char", "end_char", "confidence", "is_table", "table_data"):
        assert required_key in first
    assert "observed_term_counts" in payload


def test_standalone_attach_source_resolves_cas_from_platform_state_cas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_env: Path,
) -> None:
    """Standalone `attach-source` resolves `cas://<sha256>` stored in platform/state CAS (.opendv/state/cas)."""
    from opendv_cli.main import app_main
    from opendv_service.studio.connector_ingestion_helpers import resolve_cas_connector_bytes
    from opendv_service.studio.session_manager import StudioSessionManager
    from opendv_telemetry.cas_store import ContentAddressableStore

    ws_root = tmp_path / "workspace"
    state_dir = ws_root / ".opendv" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("OPENDV_STATE_DIR", str(state_dir))
    monkeypatch.setenv("OPENDV_STORAGE_ROOT", str(state_dir))

    csv_bytes = b"trade_id,symbol,notional\nT-1,AAPL,1000.5\nT-2,MSFT,2500.0\n"
    sha_upper = ContentAddressableStore(state_dir / "cas").put(csv_bytes)
    sha_lower = sha_upper.lower()

    assert app_main(["ontology", "studio", "init", "--json"]) == 0
    assert (
        app_main(["ontology", "studio", "create", "--session", "cas-attach-01", "--json"]) == 0
    )

    # Direct helper fallback check when platform_cas=None (before attach-source copies to session CAS)
    mgr = StudioSessionManager(studio_root=isolated_env, workspace_root=ws_root)
    raw_fallback, resolved_sha = resolve_cas_connector_bytes(
        cas_hash=sha_lower, session_mgr=mgr, session_id="cas-attach-01", platform_cas=None
    )
    assert raw_fallback == csv_bytes
    assert resolved_sha == sha_lower

    rc = app_main(
        [
            "ontology",
            "studio",
            "attach-source",
            "--session",
            "cas-attach-01",
            "--adapter",
            "csv",
            "--uri",
            f"cas://{sha_lower}",
            "--table",
            "trades",
            "--json",
        ]
    )
    assert rc == 0
