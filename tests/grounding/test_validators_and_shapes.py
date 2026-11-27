"""M2.1 tests for 13 validators, ColumnShapeStats, 6 connectors, and grounding_adapter."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from openpyxl import Workbook

from opendv.grounding.kernel.pii_core import MASKED_PII_TOKEN
from opendv.grounding.structured import (
    PATTERN_VALIDATORS,
    STRUCTURAL_PATTERN_KEYS,
    compute_column_shape_stats,
    compute_structural_patterns,
    is_valid_aba_routing,
    is_valid_cusip,
    is_valid_figi,
    is_valid_iban,
    is_valid_isin,
    is_valid_iso_3166_country,
    is_valid_iso_4217_ccy,
    is_valid_iso_date,
    is_valid_iso_timestamp,
    is_valid_lei,
    is_valid_sedol,
    is_valid_swift_bic,
    is_valid_uuid_v4,
)
from opendv.models.grounding import GroundingPolicy
from opendv.models.sources import ColumnProfile, TableProfile
from opendv.sources.adapters import extract_structured_source
from opendv.sources.grounding_adapter import (
    column_profile_to_evidence,
    source_profile_to_grounding_request,
    source_profiles_to_grounding_request,
    table_profile_to_evidence,
)
from opendv.sources.profiler import SHAPE_SAMPLE_LIMIT, preview_table, profile_source

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_AM = REPO_ROOT / "data" / "public_am"

EXPECTED_PUBLIC_AM_FINGERPRINTS: dict[str, tuple[str, str]] = {
    "am_sec_treasury_ibor.sqlite": (
        "sqlite",
        "82aef954e84f64559b0707bc7dbcbb241482d91871a4fab829f0dfa6fba01af0",
    ),
    "ds_cegar_12trap.duckdb": (
        "duckdb",
        "3c61dffce70acb35ba48f830eee46aaee93c426e03312de4510fb96125e6bd98",
    ),
    "am_esg_stewardship_proxy.parquet": (
        "parquet",
        "489621dc00713777545976104bc7c8c8884c23296c8e752f9e63cf7343722887",
    ),
    "wave2_eu_custody_lei_and_drift.parquet": (
        "parquet",
        "174289b489469d13896fbe3e9ae21f6df536c2beea1f22ddc0a98da2e50a08f7",
    ),
    "wave2_ambiguous_otc_collateral.csv": (
        "csv",
        "360491559024092d85aa371372c2c67cdb32754d6eeec37ba08edd1d02d4d0c7",
    ),
    "wave2_bitemporal_nav_restatements.csv": (
        "csv",
        "272850da02c4080f36725e4760d15397b1812a7174a6afe24019758eddbb1e9b",
    ),
    "prime_allocations_w2.csv": (
        "csv",
        "93235b2412701d142f382473046dde1f049e56fcec84314c7be1cad39451698e",
    ),
    "sec_13f_holdings.csv": (
        "csv",
        "aa74007211b5dc126f568de373f492b2c3ce2d732f9d1302b850a3a3c987055c",
    ),
    "sec_security_master.csv": (
        "csv",
        "5cdfbe7c865cdfab01e7c471046a80dfad37ed64cccebf18fab7675b5b6c7eff",
    ),
    "fred_treasury_rates.csv": (
        "csv",
        "283d1db9306b60bf790d8f209c5863bf41a8bd16a194769672832a04ba75fc27",
    ),
}


def test_all_13_structural_validators_valid_and_invalid() -> None:
    """All 13 check-digit and format validators enforce strict validity."""
    assert len(STRUCTURAL_PATTERN_KEYS) == 13
    assert set(PATTERN_VALIDATORS) == set(STRUCTURAL_PATTERN_KEYS)

    # 1. LEI (ISO 17442)
    assert is_valid_lei("5493001KJTIIGC8Y1R12") is True
    assert is_valid_lei("54930010000000000010") is False
    # 2. ISIN (ISO 6166)
    assert is_valid_isin("US0378331005") is True
    assert is_valid_isin("US0378331006") is False
    # 3. CUSIP
    assert is_valid_cusip("037833100") is True
    assert is_valid_cusip("037833101") is False
    # 4. SEDOL
    assert is_valid_sedol("0263494") is True
    assert is_valid_sedol("0263495") is False
    assert is_valid_sedol("A263494") is False  # vowel forbidden
    # 5. IBAN
    assert is_valid_iban("DE89 3704 0044 0532 0130 00") is True
    assert is_valid_iban("DE89370400440532013001") is False
    # 6. ABA routing
    assert is_valid_aba_routing("021000021") is True
    assert is_valid_aba_routing("202601015") is False
    # 7. SWIFT BIC
    assert is_valid_swift_bic("DEUTDEFF") is True
    assert is_valid_swift_bic("DEUTDEFFXXX") is True
    assert is_valid_swift_bic("DEUTZZFF") is False
    # 8. FIGI
    assert is_valid_figi("BBG000B9XRY4") is True
    assert is_valid_figi("BBG000B9XRY5") is False
    # 9. ISO 4217 Currency
    assert is_valid_iso_4217_ccy("USD") is True
    assert is_valid_iso_4217_ccy("usd") is False
    assert is_valid_iso_4217_ccy("ZZZ") is False
    # 10. ISO 3166-1 Country
    assert is_valid_iso_3166_country("US") is True
    assert is_valid_iso_3166_country("USA") is True
    assert is_valid_iso_3166_country("ZZ") is False
    # 11. UUID v4
    assert is_valid_uuid_v4("550e8400-e29b-41d4-a716-446655440000") is True
    assert is_valid_uuid_v4("550e8400-e29b-11d4-a716-446655440000") is False
    # 12. ISO Date
    assert is_valid_iso_date("2026-11-26") is True
    assert is_valid_iso_date("2026-02-30") is False
    # 13. ISO Timestamp (rejects bare dates)
    assert is_valid_iso_timestamp("2026-11-26T14:30:00Z") is True
    assert is_valid_iso_timestamp("2026-11-26 14:30:00+00:00") is True
    assert is_valid_iso_timestamp("2026-11-26") is False

    ratios = compute_structural_patterns(["USD", "EUR", "NOT_CCY", "GBP"])
    assert ratios["iso_4217_ccy"] == 0.75


def test_strict_enum_filter_sentinels_surrogates_and_rows_1_200_pii() -> None:
    """Verify strict enum filtering, gated -1/-2 sentinels, surrogates, and PII clearing."""
    # Strict code-token filter: rejects whitespace, names, and >=6-digit numbers
    human_names = ["Alice Smith", "Bob Jones"] * 25
    s_names = compute_column_shape_stats(human_names, row_count_est=50, distinct_count_est=2)
    assert s_names.looks_like_enum is True
    assert s_names.observed_enum_values == []

    long_digits = ["123456", "654321"] * 25
    s_digits = compute_column_shape_stats(long_digits, row_count_est=50, distinct_count_est=2)
    assert s_digits.looks_like_enum is True
    assert s_digits.observed_enum_values == []

    valid_codes = ["USD", "EUR", "GBP"] * 20
    s_codes = compute_column_shape_stats(valid_codes, row_count_est=60, distinct_count_est=3)
    assert s_codes.looks_like_enum is True
    assert s_codes.observed_enum_values == ["EUR", "GBP", "USD"]
    assert s_codes.structural_patterns["iso_4217_ccy"] == 1.0

    # Gated -1 / -2 sentinels: stripped on positive int/code domain with ratio <= 0.10
    int_samples = [str(i) for i in range(1, 20)] + ["-1", "-2"]
    s_int = compute_column_shape_stats(int_samples, sql_type="INTEGER")
    assert "-1" in s_int.detected_sentinels and "-2" in s_int.detected_sentinels
    assert s_int.numeric_min == 1.0 and s_int.numeric_max == 19.0

    # Continuous floats retain -1 / -2 as domain values
    float_samples = [1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5, 10.5, -1.0]
    s_flt = compute_column_shape_stats(float_samples, sql_type="DOUBLE")
    assert "-1" not in s_flt.detected_sentinels
    assert s_flt.numeric_min == -1.0

    # High ratio (> 0.10) retains -1
    s_high_neg = compute_column_shape_stats(["10", "20", "-1", "-1"], sql_type="INTEGER")
    assert "-1" not in s_high_neg.detected_sentinels
    assert s_high_neg.numeric_min == -1.0

    # Surrogate kinds (requires n >= 10 and ratio >= 0.90)
    uuids = [f"550e8400-e29b-41d4-a716-44665544{i:04d}" for i in range(12)]
    assert compute_column_shape_stats(uuids).surrogate_kind == "random_uuid_v4"

    seq = [str(i) for i in range(100, 115)]
    assert compute_column_shape_stats(seq, sql_type="BIGINT").surrogate_kind == "monotonic_int"

    zpad = [f"0000320{i:03d}" for i in range(15)]
    s_zpad = compute_column_shape_stats(zpad, sql_type="VARCHAR")
    assert s_zpad.surrogate_kind == "none"
    assert s_zpad.numeric_min is None

    # BUG-4: Non-numeric SQL types containing "INT" (INTERVAL, POINT) preserve leading-zero guard
    for non_num_type in ("INTERVAL", "INTERVAL DAY TO SECOND", "POINT"):
        s_non_num = compute_column_shape_stats(["01", "02", "03"], sql_type=non_num_type)
        assert s_non_num.numeric_min is None
        assert s_non_num.numeric_max is None
    s_real_int = compute_column_shape_stats(["01", "02", "03"], sql_type="INTEGER")
    assert s_real_int.numeric_min == 1.0 and s_real_int.numeric_max == 3.0

    op_ids = [f"ORD-2026-{i:04d}" for i in range(12)]
    assert compute_column_shape_stats(op_ids).surrogate_kind == "prefixed_operational_id"

    # Below n=10 -> "none"
    assert compute_column_shape_stats(uuids[:9]).surrogate_kind == "none"

    # Rows 1-200 PII hit clears min/max and observed_enum_values
    mixed = ["SAFE_CODE"] * 150 + ["steward@am-example.com"]
    s_pii = compute_column_shape_stats(mixed, col_name="notes", row_count_est=151, distinct_count_est=2)
    assert s_pii.pii_suppressed is True
    assert s_pii.length_min is None and s_pii.length_max is None
    assert s_pii.numeric_min is None and s_pii.numeric_max is None
    assert s_pii.observed_enum_values == []


def test_single_pass_shape_samples_and_preview_across_all_6_connectors(tmp_path: Path) -> None:
    """All 6 connectors support shape_sample_limit=200 (raw_samples<=5) and preview_table limit=0."""
    n_rows = 210
    rows = [
        (i + 1, "USD" if i < 150 else "alice@example.com")
        for i in range(n_rows)
    ]

    # 1. SQLite
    sqlite_path = tmp_path / "test.sqlite"
    with sqlite3.connect(sqlite_path) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
        conn.executemany("INSERT INTO t VALUES (?, ?)", rows)
        conn.commit()

    # 2. DuckDB
    duck_path = tmp_path / "test.duckdb"
    dconn = duckdb.connect(str(duck_path))
    dconn.execute("CREATE TABLE t (id BIGINT PRIMARY KEY, val VARCHAR)")
    dconn.executemany("INSERT INTO t VALUES (?, ?)", rows)
    dconn.close()

    # 3. Parquet
    pq_path = tmp_path / "test.parquet"
    pq.write_table(
        pa.table({"id": [r[0] for r in rows], "val": [r[1] for r in rows]}),
        pq_path,
    )

    # 4. CSV
    csv_path = tmp_path / "test.csv"
    csv_path.write_text(
        "id,val\n" + "\n".join(f"{r[0]},{r[1]}" for r in rows) + "\n",
        encoding="utf-8",
    )

    # 5. XLSX
    xlsx_path = tmp_path / "test.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "t"
    ws.append(["id", "val"])
    for r in rows:
        ws.append(list(r))
    wb.save(xlsx_path)

    for kind, fpath in (
        ("sqlite", sqlite_path),
        ("duckdb", duck_path),
        ("parquet", pq_path),
        ("csv", csv_path),
        ("xlsx", xlsx_path),
    ):
        extracted = extract_structured_source(kind, str(fpath), sample_limit=5, shape_sample_limit=200)
        cols = {c.col_name: c for c in extracted[0].columns}
        assert len(cols["id"].raw_samples) == 5
        assert len(cols["id"].shape_samples) == SHAPE_SAMPLE_LIMIT
        assert len(cols["val"].shape_samples) == SHAPE_SAMPLE_LIMIT

        # profile_source sees row 151 email in shape_samples -> pii_suppressed=True, bounds cleared,
        # while col.pii_tags (based on rows 1-5) remains []
        prof = profile_source(f"src_{kind}", kind, str(fpath))
        p_cols = {c.col_name: c for c in prof.tables[0].columns}
        assert p_cols["val"].pii_tags == []
        assert p_cols["val"].shape_stats is not None
        assert p_cols["val"].shape_stats.pii_suppressed is True
        assert p_cols["val"].shape_stats.length_min is None
        assert p_cols["val"].shape_stats.observed_enum_values == []
        assert p_cols["id"].shape_stats is not None
        assert p_cols["id"].shape_stats.surrogate_kind == "monotonic_int"

        # preview_table passes shape_sample_limit=0 & include_shape_stats=False
        prev_raw = extract_structured_source(
            kind, str(fpath), sample_limit=5, preview_offset=0, preview_limit=2, shape_sample_limit=0
        )
        assert prev_raw[0].columns[0].shape_samples == []
        page = preview_table(
            f"src_{kind}", kind, str(fpath), prof.tables[0].table_name, offset=0, limit=2
        )
        assert len(page.rows) == 2

    # 6. Databricks UC (mocked) preserves LIMIT 2 on preview_table(offset=1, limit=1) & 4 fetchalls
    mock_cur = MagicMock()
    mock_cur.fetchall.side_effect = [
        [("t",)],
        [("id", "BIGINT", "NO"), ("val", "STRING", "YES")],
        [],
        [(1, "USD"), (2, "EUR")],
    ]
    mock_cur.fetchone.side_effect = [(2,), (0, 2), (0, 2)]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    page = preview_table(
        "src_uc",
        "databricks_uc",
        "databricks://prod/core",
        "t",
        options={"catalog": "prod", "schema": "core", "_connection": mock_conn},
        offset=1,
        limit=1,
    )
    assert page.rows == [["2", "EUR"]]
    executed_sqls = [str(call.args[0]) for call in mock_cur.execute.call_args_list]
    assert any("LIMIT 2" in sql for sql in executed_sqls)

    # Profile with shape_sample_limit=200 on Databricks UC
    mock_cur_p = MagicMock()
    mock_cur_p.fetchall.side_effect = [
        [("t",)],
        [("id", "BIGINT", "NO"), ("val", "STRING", "YES")],
        [],
        [(i + 1, "USD") for i in range(10)],
    ]
    mock_cur_p.fetchone.side_effect = [(10,), (0, 10), (0, 1)]
    mock_conn_p = MagicMock()
    mock_conn_p.cursor.return_value = mock_cur_p
    uc_prof = profile_source(
        "src_uc",
        "databricks_uc",
        "databricks://prod/core",
        options={"catalog": "prod", "schema": "core", "_connection": mock_conn_p},
    )
    uc_cols = {c.col_name: c for c in uc_prof.tables[0].columns}
    assert uc_cols["val"].shape_stats is not None
    assert uc_cols["val"].shape_stats.structural_patterns.get("iso_4217_ccy") == 1.0


def test_public_am_schema_fingerprints_and_pii_tags_invariant() -> None:
    """All 10 data/public_am/* tabular files preserve exact schema_fingerprint and pii_tags."""
    for filename, (kind, expected_fp) in EXPECTED_PUBLIC_AM_FINGERPRINTS.items():
        fpath = PUBLIC_AM / filename
        prof = profile_source(f"chk_{filename}", kind, str(fpath))  # type: ignore[arg-type]
        assert prof.schema_fingerprint == expected_fp, f"Fingerprint drift on {filename}"
        for tbl in prof.tables:
            for col in tbl.columns:
                assert col.shape_stats is not None
                if col.pii_tags:
                    assert col.shape_stats.pii_suppressed is True
                    assert all(v == MASKED_PII_TOKEN for v in col.sample_values if v is not None)


def test_grounding_adapter_converts_profiles_and_precomputed_shapes() -> None:
    """grounding_adapter maps ColumnProfile/TableProfile/SourceProfile into GroundingRequest."""
    fpath = PUBLIC_AM / "sec_13f_holdings.csv"
    prof = profile_source("src_13f", "csv", str(fpath))
    tbl = prof.tables[0]

    col_ev = column_profile_to_evidence(tbl.columns[0], steward_note="Primary identifier")
    assert col_ev.precomputed_shape == tbl.columns[0].shape_stats
    assert col_ev.steward_note == "Primary identifier"

    tbl_ev = table_profile_to_evidence(
        tbl,
        steward_notes=["13F holdings"],
        column_notes={tbl.columns[0].col_name: "Key note"},
    )
    assert tbl_ev.table_id == tbl.table_id
    assert tbl_ev.steward_notes == ["13F holdings"]
    assert tbl_ev.columns[0].steward_note == "Key note"

    req = source_profile_to_grounding_request(
        prof,
        table_ids={tbl.table_id},
        table_steward_notes={tbl.table_id: ["Table note"]},
        column_steward_notes={(tbl.table_id, tbl.columns[0].col_name): "Col note"},
        global_steward_notes=["Global note"],
        policy=GroundingPolicy(mode="deterministic_only", skip_reason="adapter test"),
    )
    assert len(req.tables) == 1
    assert req.global_steward_notes == ["Global note"]
    assert req.tables[0].columns[0].steward_note == "Col note"

    # Empty table_ids filter produces empty request cleanly
    empty_req = source_profiles_to_grounding_request(
        [prof],
        table_ids={"nonexistent"},
    )
    assert empty_req.tables == []

    # Synthetic column check
    fake_col = ColumnProfile(
        col_name="c",
        sql_type="VARCHAR",
        nullable=True,
        is_pk=False,
        null_ratio=0.0,
        distinct_count_est=1,
    )
    fake_tbl = TableProfile(table_id="s:t", table_name="t", row_count_est=1, columns=[fake_col])
    assert table_profile_to_evidence(fake_tbl).columns[0].precomputed_shape is None
