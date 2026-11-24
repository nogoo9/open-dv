"""Tests for V2 source adapters, bounded profiler, SSRF guards, and DocumentMapBuilder."""

from __future__ import annotations

import hashlib
import io
import struct
import sys
import time
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import docx
import httpx
import openpyxl
import pytest

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.sources.adapters import (
    SOURCE_ADAPTERS,
    UPLOAD_EXT_TO_KIND,
    infer_kind_from_filename,
    list_source_adapters,
    open_databricks_connection,
    probe_databricks_https_endpoint,
    validate_databricks_host,
    validate_databricks_options,
    validate_sql_identifier,
)
from opendv.sources.document_map import (
    DocumentMapBuilder,
    compute_canonical_text_sha256,
    is_page_scanned_or_low_density,
    normalize_page_text,
)
from opendv.sources.profiler import preview_table, profile_source

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PUBLIC_AM = REPO_ROOT / "data" / "public_am"
SAMPLE_PDFS = REPO_ROOT / "examples" / "sample_pdfs"
SRC_MATRIX = REPO_ROOT / "examples" / "scenarios" / "src_matrix"


def test_static_source_adapters_and_field_specs() -> None:
    """UAT-S2-06 & UAT-S2-03: Static 6-adapter registry and ConnectorFieldSpec contracts."""
    adapters = list_source_adapters()
    assert len(adapters) == 6
    assert [a.adapter_id for a in adapters] == [
        "sqlite",
        "duckdb",
        "parquet",
        "csv",
        "xlsx",
        "databricks_uc",
    ]
    assert len(SOURCE_ADAPTERS) == 6

    uc = next(a for a in adapters if a.adapter_id == "databricks_uc")
    uc_keys = {f.key: f for f in uc.fields}
    assert set(uc_keys) >= {"host", "http_path", "catalog", "schema", "DATABRICKS_TOKEN", "selected_tables"}
    assert uc_keys["DATABRICKS_TOKEN"].input_kind == "secret"
    assert uc_keys["DATABRICKS_TOKEN"].secret_ref is True

    assert UPLOAD_EXT_TO_KIND[".sqlite"] == "sqlite"
    assert UPLOAD_EXT_TO_KIND[".duckdb"] == "duckdb"
    assert UPLOAD_EXT_TO_KIND[".xlsx"] == "xlsx"
    assert infer_kind_from_filename("book.xlsm") == "xlsx"
    with pytest.raises(OpenDVError) as exc_info:
        infer_kind_from_filename("archive.tar.gz")
    assert exc_info.value.error_code == ErrorCode.UNSUPPORTED_MEDIA_TYPE


def test_profile_real_am_sqlite_pks_fks_and_selected_tables() -> None:
    """UAT-S2-02 & UAT-S2-04: Profile am_sec_treasury_ibor.sqlite PKs, composite PK, FKs."""
    db_path = PUBLIC_AM / "am_sec_treasury_ibor.sqlite"
    prof = profile_source("src_am_sqlite", "sqlite", str(db_path))
    assert prof.table_count == 6
    by_name = {t.table_name: t for t in prof.tables}
    assert set(by_name) == {
        "issuers",
        "securities",
        "institutional_filers",
        "holdings_13f",
        "treasury_rates",
        "yield_curves",
    }

    treasury_cols = {c.col_name: c for c in by_name["treasury_rates"].columns}
    assert treasury_cols["observation_date"].is_pk is True
    assert treasury_cols["observation_date"].pk_ord == 1
    assert treasury_cols["series_id"].is_pk is True
    assert treasury_cols["series_id"].pk_ord == 2

    sec_cols = {c.col_name: c for c in by_name["securities"].columns}
    assert sec_cols["cik"].fk_ref == "issuers.cik"

    hold_cols = {c.col_name: c for c in by_name["holdings_13f"].columns}
    assert hold_cols["filer_cik"].fk_ref == "institutional_filers.filer_cik"
    assert hold_cols["ticker"].fk_ref == "securities.ticker"
    for col in by_name["securities"].columns:
        assert len(col.sample_values) <= 5

    filtered = profile_source(
        "src_am_sqlite",
        "sqlite",
        str(db_path),
        selected_tables=["issuers", "securities"],
    )
    assert filtered.table_count == 2
    assert {t.table_name for t in filtered.tables} == {"issuers", "securities"}
    assert filtered.schema_fingerprint != prof.schema_fingerprint

    page = preview_table("src_am_sqlite", "sqlite", str(db_path), "issuers", offset=0, limit=5)
    assert page.total_rows == 50
    assert len(page.rows) == 5


def test_profile_real_parquet_and_csvs() -> None:
    """Profile real am_esg_stewardship_proxy.parquet (30x31) and 3 AM CSV files."""
    pq_prof = profile_source(
        "src_esg",
        "parquet",
        str(PUBLIC_AM / "am_esg_stewardship_proxy.parquet"),
    )
    assert pq_prof.table_count == 1
    assert pq_prof.tables[0].row_count_est == 30
    assert len(pq_prof.tables[0].columns) == 31

    for fname, expected_rows in (
        ("fred_treasury_rates.csv", 32),
        ("sec_13f_holdings.csv", 20),
        ("sec_security_master.csv", 50),
    ):
        csv_prof = profile_source(f"src_{fname}", "csv", str(PUBLIC_AM / fname))
        assert csv_prof.table_count == 1
        assert csv_prof.tables[0].row_count_est == expected_rows


def test_profile_xlsx_via_openpyxl_and_timeout_guard(tmp_path: Path) -> None:
    """UAT-S2-02: Profile multi-sheet .xlsx via openpyxl and enforce threadpool timeout."""
    wb_path = tmp_path / "custody_fees.xlsx"
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "fee_tiers"
    ws1.append(["tier_id", "min_aum", "bps", "steward_email"])
    ws1.append([1, 1000000, 2.5, "ops@custody.example.com"])
    ws1.append([2, 5000000, 1.8, "desk@custody.example.com"])
    ws2 = wb.create_sheet("regions")
    ws2.append(["region_code", "active"])
    ws2.append(["EMEA", True])
    wb.save(wb_path)
    wb.close()

    prof = profile_source("src_xlsx", "xlsx", str(wb_path))
    assert prof.table_count == 2
    by_name = {t.table_name: t for t in prof.tables}
    assert by_name["fee_tiers"].row_count_est == 2
    cols = {c.col_name: c for c in by_name["fee_tiers"].columns}
    assert cols["tier_id"].sql_type == "BIGINT"
    assert cols["bps"].sql_type == "DOUBLE"
    assert cols["steward_email"].sample_values == ["<MASKED_PII>", "<MASKED_PII>"]

    page = preview_table("src_xlsx", "xlsx", str(wb_path), "fee_tiers")
    assert all(row[3] == "<MASKED_PII>" for row in page.rows)

    def _slow_extract(*args: object, **kwargs: object) -> list[object]:
        time.sleep(2.5)
        return []

    with patch("opendv.sources.profiler.extract_structured_source", side_effect=_slow_extract):
        with pytest.raises(OpenDVError) as exc_info:
            profile_source("src_slow", "csv", str(wb_path), profile_timeout_seconds=1)
        assert exc_info.value.error_code == ErrorCode.INTERNAL_SERVER_ERROR


def test_databricks_ssrf_cidrs_redirects_driver_missing_and_pii_tags() -> None:
    """UAT-S2-11: Databricks SSRF CIDR blocks, HTTPS/redirect guard, DRIVER_MISSING, UC PII tags."""
    blocked_targets = [
        "https://10.1.2.3/sql/1.0/warehouses/x",
        "https://172.16.0.5/sql/1.0/warehouses/x",
        "https://192.168.1.10/sql/1.0/warehouses/x",
        "https://100.64.1.5/sql/1.0/warehouses/x",
        "https://169.254.169.254/latest/meta-data",
        "https://[fc00::1]/api",
        "https://[fe80::1]/api",
        "https://127.0.0.1/api",
        "https://[::1]/api",
        "http://adb-123456.1.azuredatabricks.net",
        "localhost",
        "https://foo.localhost/api",
        "https://localhost./api",
        "user@127.0.0.1",
        "https://2130706433/api",
        "https://127.1/api",
        "https://0177.0.0.1/api",
        "127.0.0.1?x=1",
        "127.0.0.1#frag",
        "127.0.0.1\\foo",
        "127.0.0.1:443:80",
        "https://127.0.0.1\\@evil.com",
        "https://127.0.0.1@evil.com",
        "%31%32%37.0.0.1",
        "https://%31%32%37.0.0.1/api",
        "https://%31%32%37.0.0.1:443/",
        "127.0.0.%31",
        "https://127.0.0.%31/",
        "https://%6c%6f%63%61%6c%68%6f%73%74/",
        "127.0.0.1\x00",
        "https://127.0.0.1\x00",
        "https://evil.com\x00",
        "https://evil.com\x00/sql",
        "127.0.0.1\x00.evil.com",
        "https://evil.com/\x01",
        "evil.com\x7f",
        "evil.com:0",
        "https://evil.com:0/",
    ]
    for target in blocked_targets:
        for dns_flag in (False, True):
            with pytest.raises(OpenDVError) as exc_info:
                validate_databricks_host(target, resolve_dns=dns_flag)
            assert exc_info.value.error_code == ErrorCode.DATABRICKS_SSRF_BLOCKED

    with patch("opendv.sources.adapters.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("52.1.2.3", 0))]):
        assert validate_databricks_host("https://evil.com:443/") == "evil.com"

    with patch("opendv.sources.adapters.socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(2, 1, 6, "", ("100.64.10.2", 0))]
        with pytest.raises(OpenDVError) as dns_exc:
            validate_databricks_host("https://evil-cgnat.example.com")
        assert dns_exc.value.error_code == ErrorCode.DATABRICKS_SSRF_BLOCKED

    redirect_transport = httpx.MockTransport(
        lambda req: httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
    )
    with patch("opendv.sources.adapters.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("52.1.2.3", 0))]):
        with pytest.raises(OpenDVError) as redir_exc:
            probe_databricks_https_endpoint(
                "https://adb-safe.azuredatabricks.net/ping",
                transport=redirect_transport,
            )
        assert redir_exc.value.error_code == ErrorCode.DATABRICKS_SSRF_BLOCKED

    for bad_secret_key in (
        "token",
        "DATABRICKS_TOKEN",
        "client_secret",
        "bearer_token",
        "db_password",
        "databricks_pat",
        "basic_auth",
    ):
        with pytest.raises(OpenDVError) as sec_exc:
            validate_databricks_options({bad_secret_key: "dapi-plaintext"}, secret_refs=["DATABRICKS_TOKEN"])
        assert sec_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Legitimate options including http_path (contains 'pat' substring) pass cleanly
    validate_databricks_options(
        {
            "catalog": "prod",
            "schema": "core",
            "warehouse_id": "wh_1",
            "http_path": "/sql/1.0/warehouses/wh_1",
            "server_hostname": "adb-safe.azuredatabricks.net",
        },
        secret_refs=["DATABRICKS_TOKEN"],
    )

    with pytest.raises(OpenDVError) as ref_exc:
        validate_databricks_options({"catalog": "prod"}, secret_refs=[])
    assert ref_exc.value.error_code == ErrorCode.NEEDS_LOCAL_SECRET_BINDING

    with pytest.raises(OpenDVError) as id_exc:
        validate_sql_identifier("prod; DROP TABLE x")
    assert id_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    with patch("opendv.sources.adapters.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("52.1.2.3", 0))]):
        with pytest.raises(OpenDVError) as drv_exc:
            open_databricks_connection(
                host="https://adb-12345.1.azuredatabricks.net",
                http_path="/sql/1.0/warehouses/abc",
                secret_refs=["DATABRICKS_TOKEN"],
            )
        assert drv_exc.value.error_code == ErrorCode.DRIVER_MISSING

    # Emulated UC connection exercising 'pii'='true' tag masking
    mock_cur = MagicMock()
    mock_cur.fetchall.side_effect = [
        [("clients",)],  # tables
        [("client_id", "STRING", "NO"), ("gov_id", "STRING", "YES")],  # columns
        [("gov_id", "pii", "true")],  # column_tags
        [("C-1", "SECRET-ID-01")],  # preview rows
    ]
    mock_cur.fetchone.side_effect = [(1,), (0, 1), (0, 1)]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    uc_prof = profile_source(
        "src_uc",
        "databricks_uc",
        "databricks://prod/core",
        options={"catalog": "prod", "schema": "core", "_connection": mock_conn},
    )
    gov_col = next(c for c in uc_prof.tables[0].columns if c.col_name == "gov_id")
    assert "UC_TAG:PII" in gov_col.pii_tags
    assert gov_col.sample_values == ["<MASKED_PII>"]

    # Databricks UC pagination: offset=1, limit=1 must query LIMIT 2 and return row index 1
    mock_cur_page = MagicMock()
    mock_cur_page.fetchall.side_effect = [
        [("clients",)],
        [("client_id", "STRING", "NO"), ("gov_id", "STRING", "YES")],
        [("gov_id", "pii", "true")],
        [("C-1", "SECRET-ID-01"), ("C-2", "SECRET-ID-02")],
    ]
    mock_cur_page.fetchone.side_effect = [(2,), (0, 2), (0, 2)]
    mock_conn_page = MagicMock()
    mock_conn_page.cursor.return_value = mock_cur_page
    uc_page = preview_table(
        "src_uc",
        "databricks_uc",
        "databricks://prod/core",
        "clients",
        options={"catalog": "prod", "schema": "core", "_connection": mock_conn_page},
        offset=1,
        limit=1,
    )
    assert uc_page.rows == [["C-2", "<MASKED_PII>"]]
    executed_sqls = [str(call.args[0]) for call in mock_cur_page.execute.call_args_list]
    assert any("LIMIT 2" in sql for sql in executed_sqls)


def test_document_map_docx_markdown_and_domain_note(tmp_path: Path) -> None:
    """UAT-S2-07: Synchronous DocumentMap for .docx, .md, and inline domain_note."""
    builder = DocumentMapBuilder()

    # 1. Real data/public_am/am_stewardship_domain_notes.md
    md_path = PUBLIC_AM / "am_stewardship_domain_notes.md"
    md_map, md_pages = builder.build_from_path(
        source_id="src_md_notes",
        path=md_path,
        doc_format="markdown",
    )
    assert md_map.doc_id == "src_md_notes"
    assert md_map.page_count == 1
    assert md_map.pages[0].page_number == 1
    assert len(md_map.outline) >= 1
    views = builder.build_page_text_views(md_map, md_pages)
    assert len(views) == 1
    assert views[0].doc_id == "src_md_notes"

    # 2. Inline domain_note with CRLF normalization
    note_lf = "# Policy\nRule A   requires  review.\n"
    note_crlf = "# Policy\r\nRule A\t\t requires\t review.\r\n"
    m1, p1 = builder.build_from_bytes(
        source_id="src_note_1",
        filename="inline_note.txt",
        doc_format="domain_note",
        content=note_lf,
    )
    m2, _ = builder.build_from_bytes(
        source_id="src_note_2",
        filename="inline_note.txt",
        doc_format="domain_note",
        content=note_crlf,
    )
    assert m1.page_count == 1
    assert m1.pages[0].canonical_text_sha256 == m2.pages[0].canonical_text_sha256
    assert compute_canonical_text_sha256(note_lf) == compute_canonical_text_sha256(note_crlf)

    # 3. Real python-docx file
    docx_path = tmp_path / "mandate.docx"
    d = docx.Document()
    d.add_heading("Investment Mandate", level=1)
    d.add_paragraph("Contact steward.lead@fund-example.com for proxy overrides.")
    tbl = d.add_table(rows=2, cols=2)
    tbl.rows[0].cells[0].text = "fund_code"
    tbl.rows[0].cells[1].text = "limit_bps"
    tbl.rows[1].cells[0].text = "AM-01"
    tbl.rows[1].cells[1].text = "250"
    buf = io.BytesIO()
    d.save(buf)
    docx_path.write_bytes(buf.getvalue())

    dx_map, dx_pages = builder.build_from_path(
        source_id="src_docx",
        path=docx_path,
        doc_format="docx",
    )
    assert dx_map.doc_id == "src_docx"
    assert dx_map.page_count == 1
    assert dx_map.pages[0].page_number == 1
    assert dx_map.pages[0].table_headers == ["fund_code", "limit_bps"]
    assert dx_map.outline[0].title == "Investment Mandate"
    dx_views = builder.build_page_text_views(dx_map, dx_pages)
    assert "<MASKED_PII>" in dx_views[0].masked_text
    assert "steward.lead@fund-example.com" not in dx_views[0].masked_text


def test_scanned_page_boundaries_ocr_fallback_and_sample_pdfs() -> None:
    """UAT-S2-09, UAT-S2-10, UAT-S4-08: Scanned boundaries, real sample PDFs, CRLF/corrupt parity."""
    assert is_page_scanned_or_low_density(49, 1) is True
    assert is_page_scanned_or_low_density(50, 1) is False
    assert is_page_scanned_or_low_density(14, 0) is True
    assert is_page_scanned_or_low_density(15, 0) is False

    builder = DocumentMapBuilder()
    # Real scanned PDF test in examples/sample_pdfs/
    scanned_pdf = SAMPLE_PDFS / "institutional_scanned_schedule_ocr_test.pdf"
    sc_map, sc_pages = builder.build_from_path(
        source_id="src_scanned",
        path=scanned_pdf,
        doc_format="pdf",
    )
    assert sc_map.page_count == 2
    assert sc_map.pages[0].is_scanned_or_low_density is False
    assert sc_map.pages[1].is_scanned_or_low_density is True
    assert sc_map.pages[1].ocr_box_count == 0

    views_unapproved = builder.build_page_text_views(sc_map, sc_pages)
    assert views_unapproved[1].is_scanned is True
    assert views_unapproved[1].scan_approved is False

    # Mocked OCR path verifying ocr_box_count >= 1 + PII masking
    fake_tess = MagicMock()
    fake_tess.Output.DICT = "dict"
    fake_tess.image_to_data.return_value = {
        "text": ["Borrower", "SSN", "123-45-6789"],
        "conf": ["95", "92", "90"],
    }
    fake_pil = MagicMock()
    fake_pil_image = MagicMock()
    fake_pil.Image = fake_pil_image
    with (
        patch("opendv.sources.document_map.shutil.which", return_value="/usr/bin/tesseract"),
        patch.dict(
            sys.modules,
            {"pytesseract": fake_tess, "PIL": fake_pil, "PIL.Image": fake_pil_image},
        ),
    ):
        ocr_map, ocr_pages = builder.build_from_path(
            source_id="src_scanned_ocr",
            path=scanned_pdf,
            doc_format="pdf",
            approved_scan_pages=[2],
        )
    assert ocr_map.pages[1].ocr_box_count == 3
    assert "Borrower" in ocr_pages[1]
    assert "123-45-6789" in ocr_pages[1]
    assert ocr_map.pages[1].char_count == len(ocr_pages[1])
    assert ocr_map.pages[1].canonical_text_sha256 == compute_canonical_text_sha256(ocr_pages[1])
    assert ocr_map.pages[1].canonical_text_sha256 != sc_map.pages[1].canonical_text_sha256
    ocr_views = builder.build_page_text_views(ocr_map, ocr_pages)
    assert ocr_views[1].scan_approved is True
    assert "Borrower" in ocr_views[1].masked_text
    assert "<MASKED_PII>" in ocr_views[1].masked_text
    assert "123-45-6789" not in ocr_views[1].masked_text

    # Remaining 3 real PDFs in examples/sample_pdfs/
    for pdf_name, expected_pages in (
        ("nyfed_sr506_repo_infrastructure.pdf", 55),
        ("fdic_section3_2_loans_credit_risk.pdf", 88),
        ("occ_comptrollers_handbook_rating_credit_risk.pdf", 69),
    ):
        dmap, _ = builder.build_from_path(
            source_id=f"src_{pdf_name}",
            path=SAMPLE_PDFS / pdf_name,
            doc_format="pdf",
        )
        assert dmap.page_count == expected_pages
        assert dmap.pages[0].canonical_text_sha256

    # LF vs CRLF text hash parity in examples/scenarios/src_matrix/
    lf_text = (SRC_MATRIX / "enterprise_msa_addendum_2026.txt").read_text(encoding="utf-8")
    crlf_text = (SRC_MATRIX / "enterprise_msa_addendum_2026_crlf.txt").read_bytes().decode("utf-8")
    assert normalize_page_text(lf_text) == normalize_page_text(crlf_text)
    assert hashlib.sha256(normalize_page_text(lf_text).encode("utf-8")).hexdigest() == (
        compute_canonical_text_sha256(crlf_text)
    )

    # Corrupt PDF fails cleanly with SYNTAX_INVALID_ARGUMENT
    with pytest.raises(OpenDVError) as corrupt_exc:
        builder.build_from_path(
            source_id="src_corrupt",
            path=SRC_MATRIX / "corrupt_document.pdf",
            doc_format="pdf",
        )
    assert corrupt_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Non-UTF-8 markdown/domain_note bytes fail cleanly with SYNTAX_INVALID_ARGUMENT
    for fmt in ("markdown", "domain_note"):
        with pytest.raises(OpenDVError) as utf_exc:
            builder.build_from_bytes(
                source_id="src_bad_utf8",
                filename="bad.md",
                doc_format=fmt,
                content=b"\xff\xfe\xfd",
            )
        assert utf_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


def test_corrupt_binary_files_map_to_syntax_invalid_argument(tmp_path: Path) -> None:
    """Corrupt non-tabular files never leak raw sqlite3/duckdb/openpyxl/lxml exceptions."""
    corrupt_path = str(SRC_MATRIX / "corrupt_document.pdf")
    for kind in ("sqlite", "duckdb", "parquet", "xlsx"):
        with pytest.raises(OpenDVError) as exc_info:
            profile_source(f"src_corrupt_{kind}", kind, corrupt_path)
        assert exc_info.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    bad_xml_xlsx = tmp_path / "malformed_xml.xlsx"
    with zipfile.ZipFile(bad_xml_xlsx, "w") as zf:
        zf.writestr("[Content_Types].xml", "<not valid xml")
        zf.writestr("xl/workbook.xml", "<corrupt")
    with pytest.raises(OpenDVError) as xml_exc:
        profile_source("src_bad_xml_xlsx", "xlsx", str(bad_xml_xlsx))
    assert xml_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Corrupt deflate payload inside valid ZIP container (zlib.error -> SYNTAX_INVALID_ARGUMENT)
    valid_buf = io.BytesIO()
    wb = openpyxl.Workbook()
    wb.active.append(["col_a", "col_b"])
    wb.active.append(["v1", "v2"])
    wb.save(valid_buf)
    wb.close()
    valid_bytes = valid_buf.getvalue()

    corrupt_deflate = bytearray(valid_bytes)
    sheet_idx = valid_bytes.find(b"xl/worksheets/sheet1.xml")
    assert sheet_idx != -1
    lh_idx = valid_bytes.rfind(b"PK\x03\x04", 0, sheet_idx + 1)
    assert lh_idx != -1
    fn_len, ex_len = struct.unpack_from("<HH", corrupt_deflate, lh_idx + 26)
    corrupt_deflate[lh_idx + 30 + fn_len + ex_len + 1] ^= 0xFF
    bad_zlib_xlsx = tmp_path / "corrupt_deflate.xlsx"
    bad_zlib_xlsx.write_bytes(bytes(corrupt_deflate))
    with pytest.raises(OpenDVError) as zlib_exc:
        profile_source("src_bad_zlib_xlsx", "xlsx", str(bad_zlib_xlsx))
    assert zlib_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Encrypted ZIP member flag 0x01 (RuntimeError -> SYNTAX_INVALID_ARGUMENT)
    enc_bytes = bytearray(valid_bytes)
    pos = 0
    while (pos := enc_bytes.find(b"PK\x03\x04", pos)) != -1:
        flags = struct.unpack_from("<H", enc_bytes, pos + 6)[0]
        struct.pack_into("<H", enc_bytes, pos + 6, flags | 0x0001)
        pos += 4
    pos = 0
    while (pos := enc_bytes.find(b"PK\x01\x02", pos)) != -1:
        flags = struct.unpack_from("<H", enc_bytes, pos + 8)[0]
        struct.pack_into("<H", enc_bytes, pos + 8, flags | 0x0001)
        pos += 4
    enc_xlsx = tmp_path / "encrypted_member.xlsx"
    enc_xlsx.write_bytes(bytes(enc_bytes))
    with pytest.raises(OpenDVError) as enc_exc:
        profile_source("src_enc_xlsx", "xlsx", str(enc_xlsx))
    assert enc_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT
