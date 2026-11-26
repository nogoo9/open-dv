"""Phase 3 Sources tests: format_shape, surrogate scoring, proving samples, Databricks UC batching, containment, & cache."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import duckdb
import pytest

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.modeling_preferences import CachedTableSample
from opendv.models.pii_tokenizer import PII_TOKEN_RE, compute_salt_digest
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
from opendv.sources._extractors import extract_databricks_uc
from opendv.sources._extractors_files import RawColumnData, RawTableData
from opendv.sources.containment import compute_pk_containment_overlaps
from opendv.sources.profiler import (
    _build_column_profile,
    _classify_format_shape,
    _compute_surrogate_score,
    _to_table_profile,
    profile_source_with_samples,
)
from opendv.sources.proving_cache import (
    build_proving_cache_file,
    compute_proving_table_fingerprint,
    is_cached_table_valid,
    validate_source_id_slug,
)


def test_format_shape_surrogate_score_bk_candidate_and_freq_redaction() -> None:
    """format_shape classification, surrogate_score, composite PK exemption, & PII freq redaction."""
    assert _classify_format_shape(["2026-01-01", "2026-01-02"], "DATE") == "ISO_DATE"
    assert (
        _classify_format_shape(
            ["2026-01-01T12:00:00Z", "2026-01-02 08:30:00+00:00"], "TIMESTAMP"
        )
        == "ISO_TIMESTAMP"
    )
    assert (
        _classify_format_shape(
            ["550e8400-e29b-41d4-a716-446655440000", "6ba7b810-9dad-11d1-80b4-00c04fd430c8"],
            "VARCHAR",
        )
        == "UUID"
    )
    assert (
        _classify_format_shape(["alice@example.com", "bob@corp.org"], "VARCHAR") == "EMAIL"
    )
    assert _classify_format_shape(["1", "2", "3", "4"], "INTEGER") == "INT_SEQ"
    assert _classify_format_shape(["10.50", "20.75"], "DECIMAL(10,2)") == "DECIMAL"
    assert _classify_format_shape(["10", "20"], "NUMERIC(10,2)") == "DECIMAL"
    assert _classify_format_shape(["US-001", "EU-002", "APAC_9"], "VARCHAR") == "CODE_UPPER"

    # surrogate_score: surrogate name + monotonic INT_SEQ / UUID + no delimiters >= 0.75
    assert _compute_surrogate_score("id", "INT_SEQ", ["1", "2", "3", "4"]) == 1.0
    assert (
        _compute_surrogate_score(
            "row_id",
            "UUID",
            ["550e8400-e29b-41d4-a716-446655440000", "6ba7b810-9dad-11d1-80b4-00c04fd430c8"],
        )
        == 0.8
    )
    assert _compute_surrogate_score("acct_code", "CODE_UPPER", ["AC-01", "AC-02"]) == 0.0

    # Surrogate-prefiltered best_non_surrogate_bk_candidate_col (single PK skipped, natural BK chosen)
    raw_single_pk = RawTableData(
        table_name="accounts",
        row_count_est=4,
        columns=[
            RawColumnData(
                col_name="id",
                sql_type="INTEGER",
                nullable=False,
                is_pk=True,
                pk_ord=1,
                fk_ref=None,
                null_ratio=0.0,
                distinct_count_est=4,
                raw_samples=["1", "2", "3", "4"],
            ),
            RawColumnData(
                col_name="acct_code",
                sql_type="VARCHAR",
                nullable=False,
                is_pk=False,
                pk_ord=None,
                fk_ref=None,
                null_ratio=0.0,
                distinct_count_est=4,
                raw_samples=["AC-01", "AC-02", "AC-03", "AC-04"],
            ),
        ],
        preview_rows=[
            ["1", "AC-01"],
            ["2", "AC-02"],
            ["3", "AC-03"],
            ["4", "AC-04"],
        ],
    )
    prof_single = _to_table_profile("src_a", raw_single_pk)
    assert prof_single.best_non_surrogate_bk_candidate_col == "acct_code"
    assert prof_single.best_non_surrogate_bk_uniqueness_ratio == 1.0

    # Composite PK exemption: surrogate-looking PK columns are NOT excluded when len(pk_cols) > 1
    raw_comp_pk = RawTableData(
        table_name="order_lines",
        row_count_est=3,
        columns=[
            RawColumnData(
                col_name="id",
                sql_type="INTEGER",
                nullable=False,
                is_pk=True,
                pk_ord=1,
                fk_ref=None,
                null_ratio=0.0,
                distinct_count_est=3,
                raw_samples=["1", "2", "3"],
            ),
            RawColumnData(
                col_name="line_no",
                sql_type="INTEGER",
                nullable=False,
                is_pk=True,
                pk_ord=2,
                fk_ref=None,
                null_ratio=0.0,
                distinct_count_est=2,
                raw_samples=["1", "2", "1"],
            ),
            RawColumnData(
                col_name="status",
                sql_type="VARCHAR",
                nullable=False,
                is_pk=False,
                pk_ord=None,
                fk_ref=None,
                null_ratio=0.0,
                distinct_count_est=1,
                raw_samples=["OPEN", "OPEN", "OPEN"],
            ),
        ],
        preview_rows=[["1", "1", "OPEN"], ["2", "2", "OPEN"], ["3", "1", "OPEN"]],
    )
    prof_comp = _to_table_profile("src_a", raw_comp_pk)
    assert prof_comp.best_non_surrogate_bk_candidate_col == "id"

    # PII-redacted top_value_frequencies vs non-PII top_value_frequencies
    non_pii_cp = _build_column_profile(
        RawColumnData(
            col_name="ccy",
            sql_type="VARCHAR",
            nullable=False,
            is_pk=False,
            pk_ord=None,
            fk_ref=None,
            null_ratio=0.0,
            distinct_count_est=2,
            raw_samples=["USD", "EUR", "USD"],
        ),
        observed_values=["USD", "EUR", "USD"],
    )
    assert [(item.value, item.count) for item in non_pii_cp.top_value_frequencies] == [
        ("USD", 2),
        ("EUR", 1),
    ]

    pii_cp = _build_column_profile(
        RawColumnData(
            col_name="owner_email",
            sql_type="VARCHAR",
            nullable=False,
            is_pk=False,
            pk_ord=None,
            fk_ref=None,
            null_ratio=0.0,
            distinct_count_est=2,
            raw_samples=["alice@example.com", "bob@example.com", "alice@example.com"],
        ),
        observed_values=["alice@example.com", "bob@example.com", "alice@example.com"],
    )
    assert pii_cp.pii_tags
    assert [(item.value, item.count) for item in pii_cp.top_value_frequencies] == [
        ("<MASKED_PII_0>", 2),
        ("<MASKED_PII_1>", 1),
    ]


def test_profile_source_with_samples_bounds_pii_opt_out_and_rowid_guards(tmp_path: Path) -> None:
    """proving_sample_rows bounds, is_complete_table, idempotent PII tokens, opt-out, & VIEW/WITHOUT ROWID."""
    csv_path = tmp_path / "clients.csv"
    lines = ["client_id,email,notes"]
    for i in range(6):
        lines.append(f"C{i},user{i}@example.com,note for alice@example.com")
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 1. Invalid proving_sample_rows bounds (including bool)
    for bad in (0, -1, 5001, True):
        with pytest.raises(OpenDVError) as exc_info:
            profile_source_with_samples("src_csv", "csv", str(csv_path), proving_sample_rows=bad)  # type: ignore[arg-type]
        assert exc_info.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # 2. real_row_proving=False opt-out
    prof_off, rows_off, flags_off = profile_source_with_samples(
        "src_csv", "csv", str(csv_path), real_row_proving=False
    )
    assert prof_off.table_count == 1
    assert rows_off == {}
    assert flags_off == {}

    # 3. Partial (6 rows > limit=5 -> is_complete_table=False) vs Complete (limit=6 -> True)
    prof_p, rows_p, flags_p = profile_source_with_samples(
        "src_csv", "csv", str(csv_path), proving_sample_rows=5, pii_salt="fixed-salt-v1"
    )
    tid = prof_p.tables[0].table_id
    assert len(rows_p[tid]) == 5
    assert flags_p[tid] is False

    prof_c1, rows_c1, flags_c1 = profile_source_with_samples(
        "src_csv", "csv", str(csv_path), proving_sample_rows=6, pii_salt="fixed-salt-v1"
    )
    prof_c2, rows_c2, flags_c2 = profile_source_with_samples(
        "src_csv", "csv", str(csv_path), proving_sample_rows=6, pii_salt="fixed-salt-v1"
    )
    assert flags_c1[tid] is True
    assert len(rows_c1[tid]) == 6
    assert rows_c1[tid] == rows_c2[tid]  # idempotent with same salt
    for row in rows_c1[tid]:
        assert PII_TOKEN_RE.fullmatch(str(row["email"]))
        assert PII_TOKEN_RE.fullmatch(str(row["notes"]))  # inline PII value tokenized

    # Different salt produces different tokens
    _, rows_diff_salt, _ = profile_source_with_samples(
        "src_csv", "csv", str(csv_path), proving_sample_rows=6, pii_salt="other-salt-v2"
    )
    assert rows_diff_salt[tid][0]["email"] != rows_c1[tid][0]["email"]

    # 4. SQLite VIEW + WITHOUT ROWID and DuckDB VIEW rowid guard
    sqlite_path = tmp_path / "guard.sqlite"
    sconn = sqlite3.connect(str(sqlite_path))
    try:
        sconn.execute("CREATE TABLE base_tbl (k TEXT PRIMARY KEY, v INTEGER) WITHOUT ROWID")
        sconn.execute("INSERT INTO base_tbl VALUES ('A', 1), ('B', 2)")
        sconn.execute("CREATE VIEW v_base AS SELECT k, v FROM base_tbl")
        sconn.commit()
    finally:
        sconn.close()

    s_prof, s_rows, s_flags = profile_source_with_samples(
        "src_sq", "sqlite", str(sqlite_path), proving_sample_rows=10
    )
    assert {t.table_name for t in s_prof.tables} == {"base_tbl", "v_base"}
    assert all(len(s_rows[t.table_id]) == 2 for t in s_prof.tables)
    assert all(s_flags[t.table_id] is True for t in s_prof.tables)

    duck_path = tmp_path / "guard.duckdb"
    dconn = duckdb.connect(str(duck_path))
    try:
        dconn.execute("CREATE TABLE base_t (k VARCHAR PRIMARY KEY, v INT)")
        dconn.execute("INSERT INTO base_t VALUES ('X', 10), ('Y', 20)")
        dconn.execute("CREATE VIEW v_t AS SELECT k, v FROM base_t")
    finally:
        dconn.close()

    d_prof, d_rows, d_flags = profile_source_with_samples(
        "src_dk", "duckdb", str(duck_path), proving_sample_rows=10
    )
    assert {t.table_name for t in d_prof.tables} == {"base_t", "v_t"}
    assert all(len(d_rows[t.table_id]) == 2 for t in d_prof.tables)
    assert all(d_flags[t.table_id] is True for t in d_prof.tables)


def test_extract_databricks_uc_100col_batching_and_complex_type_exclusion() -> None:
    """extract_databricks_uc batches aggregate queries by 100 cols & skips APPROX_COUNT_DISTINCT on MAP/ARRAY/STRUCT."""
    col_defs: list[tuple[str, str, str]] = [
        ("col_map", "MAP<STRING, STRING>", "YES"),
        ("col_arr", "ARRAY<INT>", "YES"),
        ("col_struct", "STRUCT<a: INT, b: STRING>", "YES"),
    ]
    for i in range(202):  # total 205 columns -> 3 aggregate chunks (100, 100, 5)
        col_defs.append((f"c_{i:03d}", "STRING", "YES"))

    class _FakeCursor:
        def __init__(self) -> None:
            self.executed_sql: list[str] = []
            self._next_rows: list[tuple[Any, ...]] = []

        def execute(self, sql: str) -> None:
            self.executed_sql.append(sql)
            if "information_schema.tables" in sql:
                self._next_rows = [("wide_tbl",)]
            elif "information_schema.columns" in sql:
                self._next_rows = col_defs
            elif "information_schema.column_tags" in sql:
                self._next_rows = [("c_000", "pii", "true")]
            elif sql.startswith("SELECT count(*) FROM"):
                self._next_rows = [(42,)]
            elif sql.startswith("SELECT * FROM"):
                self._next_rows = [tuple("v" for _ in col_defs)]
            elif "APPROX_COUNT_DISTINCT" in sql or "count(*) - count(" in sql:
                num_pairs = sql.count("count(*) - count(")
                self._next_rows = [tuple(1 for _ in range(num_pairs * 2))]
            else:
                self._next_rows = []

        def fetchall(self) -> list[tuple[Any, ...]]:
            return self._next_rows

        def fetchone(self) -> tuple[Any, ...] | None:
            return self._next_rows[0] if self._next_rows else None

    class _FakeConn:
        def __init__(self) -> None:
            self.cur = _FakeCursor()

        def cursor(self) -> _FakeCursor:
            return self.cur

    fake_conn = _FakeConn()
    tables = extract_databricks_uc(
        options={"catalog": "main_cat", "schema": "fin_schema", "_connection": fake_conn},
        secret_refs=(),
        selected_tables=None,
        sample_limit=5,
        preview_offset=0,
        preview_limit=5,
        validate_ident_fn=lambda s: s,
        open_conn_fn=lambda **_kw: fake_conn,
    )
    assert len(tables) == 1
    assert len(tables[0].columns) == 205
    assert tables[0].columns[3].explicit_pii_tags == ["UC_TAG:PII"]

    agg_sqls = [s for s in fake_conn.cur.executed_sql if "count(*) - count(" in s]
    assert len(agg_sqls) == 3
    assert agg_sqls[0].count("count(*) - count(") == 100
    assert agg_sqls[1].count("count(*) - count(") == 100
    assert agg_sqls[2].count("count(*) - count(") == 5

    joined_aggs = "\n".join(agg_sqls)
    for complex_col in ("col_map", "col_arr", "col_struct"):
        assert f"count(`{complex_col}`)" in joined_aggs
        assert f"APPROX_COUNT_DISTINCT(`{complex_col}`)" not in joined_aggs
    assert "APPROX_COUNT_DISTINCT(`c_000`)" in joined_aggs


def test_compute_pk_containment_overlaps_orientation_thresholds_and_1_to_1() -> None:
    """Asymmetric FK->PK orientation, small vs large table min_hashes threshold, & zero 1:1 duplicates."""
    # 1. Small fixture (<= 20 rows -> min_hashes=2): cust_ref -> customer_id (is_pk=True), no bidirectional dup
    p_cust = TableProfile(
        table_id="s:customers",
        table_name="customers",
        row_count_est=3,
        best_non_surrogate_bk_candidate_col="cust_code",
        columns=[
            ColumnProfile(col_name="customer_id", sql_type="VARCHAR", nullable=False, is_pk=True, null_ratio=0.0, distinct_count_est=3),
            ColumnProfile(col_name="cust_code", sql_type="VARCHAR", nullable=False, is_pk=False, null_ratio=0.0, distinct_count_est=3),
        ],
    )
    p_ord = TableProfile(
        table_id="s:orders",
        table_name="orders",
        row_count_est=3,
        columns=[
            ColumnProfile(col_name="order_id", sql_type="VARCHAR", nullable=False, is_pk=True, null_ratio=0.0, distinct_count_est=3),
            ColumnProfile(col_name="cust_ref", sql_type="VARCHAR", nullable=False, is_pk=False, null_ratio=0.0, distinct_count_est=2),
        ],
    )
    p_1to1 = TableProfile(
        table_id="s:customers_ext",
        table_name="customers_ext",
        row_count_est=3,
        columns=[
            ColumnProfile(col_name="ext_id", sql_type="VARCHAR", nullable=False, is_pk=False, null_ratio=0.0, distinct_count_est=3),
        ],
    )
    prof = SourceProfile(
        source_id="s",
        schema_fingerprint="fp",
        table_count=3,
        tables=[p_cust, p_ord, p_1to1],
    )
    rows = {
        "s:customers": [
            {"customer_id": "C1", "cust_code": "K1"},
            {"customer_id": "C2", "cust_code": "K2"},
            {"customer_id": "C3", "cust_code": "K3"},
        ],
        "s:orders": [
            {"order_id": "O1", "cust_ref": "C1"},
            {"order_id": "O2", "cust_ref": "C2"},
            {"order_id": "O3", "cust_ref": "C1"},
        ],
        "s:customers_ext": [
            {"ext_id": "C1"},
            {"ext_id": "C2"},
            {"ext_id": "C3"},
        ],
    }
    overlaps = compute_pk_containment_overlaps([prof], rows)
    pairs = [(o.left_table_id, o.left_col, o.right_table_id, o.right_col) for o in overlaps]
    assert ("s:orders", "cust_ref", "s:customers", "customer_id") in pairs
    assert ("s:customers", "customer_id", "s:orders", "cust_ref") not in pairs
    # 1:1 between customers.customer_id (is_pk=True) and customers_ext.ext_id (is_pk=False) emits only 1 directed edge
    ext_edges = [
        p for p in pairs if {p[0], p[2]} == {"s:customers", "s:customers_ext"}
    ]
    assert ext_edges == [("s:customers_ext", "ext_id", "s:customers", "customer_id")]

    # 2. Large fixture (> 20 rows -> min_hashes=10): 5 distinct shared values are rejected, 10 accepted
    t_big_tgt = TableProfile(
        table_id="s:big_tgt",
        table_name="big_tgt",
        row_count_est=25,
        columns=[ColumnProfile(col_name="big_tgt_id", sql_type="VARCHAR", nullable=False, is_pk=True, null_ratio=0.0, distinct_count_est=25)],
    )
    t_big_src_5 = TableProfile(
        table_id="s:big_src_5",
        table_name="big_src_5",
        row_count_est=25,
        columns=[ColumnProfile(col_name="fk_col", sql_type="VARCHAR", nullable=False, is_pk=False, null_ratio=0.0, distinct_count_est=5)],
    )
    t_big_src_10 = TableProfile(
        table_id="s:big_src_10",
        table_name="big_src_10",
        row_count_est=25,
        columns=[ColumnProfile(col_name="fk_col", sql_type="VARCHAR", nullable=False, is_pk=False, null_ratio=0.0, distinct_count_est=10)],
    )
    prof_big = SourceProfile(
        source_id="s", schema_fingerprint="fp_big", table_count=3, tables=[t_big_tgt, t_big_src_5, t_big_src_10]
    )
    rows_big = {
        "s:big_tgt": [{"big_tgt_id": f"V{i}"} for i in range(25)],
        "s:big_src_5": [{"fk_col": f"V{i % 5}"} for i in range(25)],
        "s:big_src_10": [{"fk_col": f"V{i % 10}"} for i in range(25)],
    }
    big_overlaps = compute_pk_containment_overlaps([prof_big], rows_big)
    big_lefts = {o.left_table_id for o in big_overlaps if o.right_table_id == "s:big_tgt"}
    assert "s:big_src_5" not in big_lefts
    assert "s:big_src_10" in big_lefts


def test_proving_cache_slug_fingerprint_and_invalidation() -> None:
    """validate_source_id_slug, compute_proving_table_fingerprint determinism, & is_cached_table_valid."""
    assert validate_source_id_slug("crm_prod-01.eu") == "crm_prod-01.eu"
    for bad_slug in ("../etc/passwd", "a/b", "a\\b", ".hidden", "", "x" * 65):
        with pytest.raises(OpenDVError) as exc_info:
            validate_source_id_slug(bad_slug)
        assert exc_info.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    col_a = ColumnProfile(col_name="acct_id", sql_type="VARCHAR", nullable=False, is_pk=True, null_ratio=0.0, distinct_count_est=2)
    col_b = ColumnProfile(col_name="email", sql_type="VARCHAR", nullable=True, is_pk=False, null_ratio=0.0, distinct_count_est=2, pii_tags=["EMAIL"])
    tbl1 = TableProfile(table_id="crm:accts", table_name="accts", row_count_est=2, columns=[col_a, col_b])
    tbl1_reordered = TableProfile(table_id="crm:accts", table_name="accts", row_count_est=2, columns=[col_b, col_a])
    tbl_changed = TableProfile(
        table_id="crm:accts",
        table_name="accts",
        row_count_est=2,
        columns=[col_a, col_b.model_copy(update={"sql_type": "BIGINT"})],
    )

    fp1 = compute_proving_table_fingerprint(tbl1)
    assert fp1 == compute_proving_table_fingerprint(tbl1_reordered)
    assert fp1 != compute_proving_table_fingerprint(tbl_changed)

    salt_digest = compute_salt_digest("salt-abc")
    cache_file = build_proving_cache_file(
        "crm",
        "proj_1",
        [tbl1],
        {"crm:accts": [{"acct_id": "A1", "email": "<PII:0123456789abcdef>"}, {"acct_id": "A2", "email": "<PII:fedcba9876543210>"}]},
        {"crm:accts": True},
        salt_key_id="k1",
        salt_digest=salt_digest,
        cached_sample_rows_limit=200,
    )
    cached = cache_file.tables["crm:accts"]
    assert is_cached_table_valid(cached, tbl1, expected_salt_digest=salt_digest, expected_limit=200) is True

    # Invalidation on schema, salt, limit, row_count, completeness, & table_id mismatch
    assert is_cached_table_valid(cached, tbl_changed, expected_salt_digest=salt_digest, expected_limit=200) is False
    assert is_cached_table_valid(cached, tbl1, expected_salt_digest="deadbeef00000000", expected_limit=200) is False
    assert is_cached_table_valid(cached, tbl1, expected_salt_digest=salt_digest, expected_limit=100) is False
    tbl_more_rows = tbl1.model_copy(update={"row_count_est": 3})
    assert is_cached_table_valid(cached, tbl_more_rows, expected_salt_digest=salt_digest, expected_limit=200) is False
    bad_complete = CachedTableSample(
        table_id=cached.table_id,
        schema_fingerprint=cached.schema_fingerprint,
        salt_digest=cached.salt_digest,
        rows=cached.rows,
        row_count_est=cached.row_count_est,
        is_complete_table=False,  # mismatch since row_count_est=2 <= limit=200
        cached_sample_rows_limit=200,
    )
    assert is_cached_table_valid(bad_complete, tbl1, expected_salt_digest=salt_digest, expected_limit=200) is False
    tbl_other_id = tbl1.model_copy(update={"table_id": "crm:other"})
    assert is_cached_table_valid(cached, tbl_other_id, expected_salt_digest=salt_digest, expected_limit=200) is False
