"""Tests for parsing sessions containing both structured connectors and unstructured documents."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from opendv_service.studio.connector_ingestion import (
    attach_connector_source,
    get_session_evidence_tables,
)
from opendv_service.studio.job_runners_evidence import load_active_session_evidence
from opendv_service.studio.job_runners_parse import run_parse_session_sources
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.session_source_workflows import persist_uploaded_source_sync
from opendv_sources.exceptions import UnsupportedFormatError


def _build_sample_sqlite(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE issuers (cik TEXT PRIMARY KEY, legal_name TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE positions ("
            "pos_id TEXT PRIMARY KEY, "
            "cik TEXT NOT NULL REFERENCES issuers(cik), "
            "notional REAL)"
        )
        conn.execute("INSERT INTO issuers VALUES ('0000320193', 'Apple Inc.')")
        conn.execute("INSERT INTO positions VALUES ('POS-1', '0000320193', 1000.0)")
        conn.commit()
    finally:
        conn.close()


def test_parse_mixed_connectors_and_unstructured_docs_preserves_connector_tables(
    tmp_path: Path,
) -> None:
    """Mixed session with SQLite/CSV connectors + PDF/MD/TXT parses cleanly and keeps tables."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    sqlite_path = ws_root / "ds01_baseline.sqlite"
    _build_sample_sqlite(sqlite_path)
    csv_path = ws_root / "ds02_custody.csv"
    csv_path.write_text("acct_id,currency\nACC-1,USD\nACC-2,EUR\n", encoding="utf-8")

    mgr = StudioSessionManager(
        studio_root=ws_root / ".opendv" / "ontology_studio", workspace_root=ws_root
    )
    mgr.create_session("sess-mixed-parse")
    sdir = mgr.get_session_dir("sess-mixed-parse")

    sqlite_res = attach_connector_source(
        session_mgr=mgr,
        session_id="sess-mixed-parse",
        request_payload={
            "adapter": "sqlite",
            "uri": str(sqlite_path),
            "source_name": "ds01_baseline",
        },
    )
    assert sqlite_res["table_count"] == 2

    csv_res = attach_connector_source(
        session_mgr=mgr,
        session_id="sess-mixed-parse",
        request_payload={
            "adapter": "file",
            "uri": str(csv_path),
            "source_name": "ds02_custody.csv",
        },
    )
    assert csv_res["table_count"] == 1

    # Upload unstructured PDF (real repo fixture), Markdown (with pipe table), and confidential TXT
    pdf_fixture = (
        Path(__file__).resolve().parents[2]
        / "opendv-sources"
        / "tests"
        / "fixtures"
        / "enterprise_msa_addendum_2026.pdf"
    )
    assert pdf_fixture.is_file()
    pdf_tmp = tmp_path / "policy.pdf"
    pdf_tmp.write_bytes(pdf_fixture.read_bytes())
    pdf_meta = persist_uploaded_source_sync(
        session_mgr=mgr,
        session_id="sess-mixed-parse",
        tmp_blob_path=pdf_tmp,
        size_bytes=pdf_tmp.stat().st_size,
        filename="policy.pdf",
        mime_type="application/pdf",
        do_not_send_to_agent=False,
        description="Regulatory policy PDF",
        principal_id="human:local_user",
        is_automated=False,
    )

    md_tmp = tmp_path / "domain-note.md"
    md_bytes = (
        b"# Settlement Rules\n\n"
        b"| Code | Description |\n"
        b"| --- | --- |\n"
        b"| T1 | Next business day settlement |\n"
    )
    md_tmp.write_bytes(md_bytes)
    md_meta = persist_uploaded_source_sync(
        session_mgr=mgr,
        session_id="sess-mixed-parse",
        tmp_blob_path=md_tmp,
        size_bytes=len(md_bytes),
        filename="domain-note.md",
        mime_type="text/markdown",
        do_not_send_to_agent=False,
        description=None,
        principal_id="human:local_user",
        is_automated=False,
    )

    txt_tmp = tmp_path / "mnpi.txt"
    txt_bytes = b"Confidential restriction: insider trading blackout window.\n"
    txt_tmp.write_bytes(txt_bytes)
    txt_meta = persist_uploaded_source_sync(
        session_mgr=mgr,
        session_id="sess-mixed-parse",
        tmp_blob_path=txt_tmp,
        size_bytes=len(txt_bytes),
        filename="mnpi.txt",
        mime_type="text/plain",
        do_not_send_to_agent=True,
        description=None,
        principal_id="human:local_user",
        is_automated=False,
    )

    parse_res = run_parse_session_sources(mgr, "sess-mixed-parse", source_ids=None)
    assert parse_res["parsed_sources_count"] == 3
    assert parse_res["chunks_count"] >= 3

    chunks, tables = load_active_session_evidence(sdir)
    chunk_source_ids = {(c.metadata or {}).get("source_id") for c in chunks}
    assert pdf_meta["source_id"] in chunk_source_ids
    assert md_meta["source_id"] in chunk_source_ids
    assert txt_meta["source_id"] in chunk_source_ids

    txt_chunks = [c for c in chunks if (c.metadata or {}).get("source_id") == txt_meta["source_id"]]
    assert txt_chunks and all(
        (c.metadata or {}).get("do_not_send_to_agent") is True for c in txt_chunks
    )

    # All 3 pre-profiled structured connector tables must remain alongside parsed doc tables
    table_names = {(t.metadata or {}).get("table_name") or t.caption for t in tables}
    assert {"issuers", "positions", "ds02_custody"}.issubset(table_names)
    assert parse_res["tables_count"] >= 1  # markdown table parsed without erasing connector tables
    assert len(tables) >= 4

    preview = get_session_evidence_tables(mgr, "sess-mixed-parse")
    preview_names = {t["table_name"] for t in preview["tables"]}
    assert {"issuers", "positions", "ds02_custody"}.issubset(preview_names)


def test_parse_session_with_only_connectors_preserves_tables(tmp_path: Path) -> None:
    """Parsing a session with only structured connectors is a clean no-op keeping tables."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    sqlite_path = ws_root / "only_conn.sqlite"
    _build_sample_sqlite(sqlite_path)

    mgr = StudioSessionManager(
        studio_root=ws_root / ".opendv" / "ontology_studio", workspace_root=ws_root
    )
    mgr.create_session("sess-conn-only")
    attach_connector_source(
        session_mgr=mgr,
        session_id="sess-conn-only",
        request_payload={
            "adapter": "sqlite",
            "uri": str(sqlite_path),
            "source_name": "only_conn",
        },
    )

    parse_res = run_parse_session_sources(mgr, "sess-conn-only", source_ids=None)
    assert parse_res == {"parsed_sources_count": 0, "chunks_count": 0, "tables_count": 0}

    _, tables = load_active_session_evidence(mgr.get_session_dir("sess-conn-only"))
    table_names = {(t.metadata or {}).get("table_name") or t.caption for t in tables}
    assert table_names == {"issuers", "positions"}


def test_parse_explicit_source_ids_behavior_on_mixed_session(tmp_path: Path) -> None:
    """Explicit source_ids parses targeted doc cleanly and fails loudly on targeted connector."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    sqlite_path = ws_root / "conn.sqlite"
    _build_sample_sqlite(sqlite_path)

    mgr = StudioSessionManager(
        studio_root=ws_root / ".opendv" / "ontology_studio", workspace_root=ws_root
    )
    mgr.create_session("sess-explicit-ids")
    conn_res = attach_connector_source(
        session_mgr=mgr,
        session_id="sess-explicit-ids",
        request_payload={
            "adapter": "sqlite",
            "uri": str(sqlite_path),
            "source_name": "conn",
        },
    )

    md_tmp = tmp_path / "notes.md"
    md_bytes = b"# Notes\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    md_tmp.write_bytes(md_bytes)
    md_meta = persist_uploaded_source_sync(
        session_mgr=mgr,
        session_id="sess-explicit-ids",
        tmp_blob_path=md_tmp,
        size_bytes=len(md_bytes),
        filename="notes.md",
        mime_type="text/markdown",
        do_not_send_to_agent=False,
        description=None,
        principal_id="human:local_user",
        is_automated=False,
    )

    # Targeting only the markdown doc parses it and preserves SQLite tables
    res = run_parse_session_sources(mgr, "sess-explicit-ids", source_ids=[md_meta["source_id"]])
    assert res["parsed_sources_count"] == 1
    assert res["tables_count"] == 1
    _, tables = load_active_session_evidence(mgr.get_session_dir("sess-explicit-ids"))
    table_names = {(t.metadata or {}).get("table_name") or t.caption for t in tables}
    assert {"issuers", "positions"}.issubset(table_names)
    assert len(tables) == 3

    # Explicitly targeting the SQLite connector still fails loudly with UnsupportedFormatError
    with pytest.raises(UnsupportedFormatError):
        run_parse_session_sources(mgr, "sess-explicit-ids", source_ids=[conn_res["source_id"]])

