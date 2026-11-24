"""Tests for Algorithm A.5 canonical hashing, sentinels, PredicateNode, and BUG-01..08 oracle."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from opendv.models import (
    DEFAULT_DELIMITER,
    NULL_TOKEN,
    ZERO_KEY_ERROR,
    ZERO_KEY_UNKNOWN,
    BusinessKeyPart,
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    DecimalType,
    DraftField,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
    HashRecipeSpec,
    IRField,
    LoadSemanticsSpec,
    PhysicalLayoutHints,
    PredicateBranch,
    PredicateLeaf,
    SchemaWaveDelta,
    SeedRecordsSpec,
    SourceBindingSpec,
    build_seed_records,
    compute_alg_a5_hash,
    compute_alg_a5_prehash,
    encode_alg_a5_token,
    evaluate_predicate,
    execute_hash_recipe,
    format_scalar_alg_a5,
    resolve_bkcc,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SQLITE_PATH = REPO_ROOT / "data" / "public_am" / "am_sec_treasury_ibor.sqlite"
PARQUET_PATH = REPO_ROOT / "data" / "public_am" / "am_esg_stewardship_proxy.parquet"


def _load_sqlite_rows(table: str) -> list[dict[str, Any]]:
    with sqlite3.connect(SQLITE_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]


def _load_parquet_rows() -> list[dict[str, Any]]:
    return pq.read_table(PARQUET_PATH).to_pylist()


class TestUtf8AndLeadingZeroPreservation:
    """BUG-01 UTF-8 octet lengths and leading-zero CIK/CUSIP preservation."""

    def test_multibyte_utf8_byte_length_and_case(self) -> None:
        name = "Nordisk Suverän Fond"
        assert len(name) == 20
        assert len(name.encode("utf-8")) == 21
        assert encode_alg_a5_token(name, is_business_key=False) == "21:Nordisk Suverän Fond"
        assert encode_alg_a5_token(name, is_business_key=True) == "21:NORDISK SUVERÄN FOND"
        assert encode_alg_a5_token("🚀", is_business_key=False) == "4:🚀"

    def test_synthetic_and_real_sqlite_leading_zeros(self) -> None:
        assert encode_alg_a5_token("0000909100") == "10:0000909100"
        assert encode_alg_a5_token("909100") == "6:909100"
        assert compute_alg_a5_hash(["0000909100"], bkcc="GLOBAL") != compute_alg_a5_hash(
            ["909100"], bkcc="GLOBAL"
        )

        issuers = {r["ticker"]: r for r in _load_sqlite_rows("issuers")}
        assert issuers["ABT"]["cik"] == "0000001800"
        assert issuers["AAPL"]["cik"] == "0000320193"
        assert issuers["BRK-B"]["cik"] == "0001067983"

        assert encode_alg_a5_token(issuers["ABT"]["cik"]) == "10:0000001800"
        assert encode_alg_a5_token(issuers["AAPL"]["cik"]) == "10:0000320193"
        assert encode_alg_a5_token(issuers["BRK-B"]["cik"]) == "10:0001067983"
        assert compute_alg_a5_hash([issuers["AAPL"]["cik"]], bkcc="GLOBAL") != compute_alg_a5_hash(
            ["320193"], bkcc="GLOBAL"
        )

        holdings = {r["holding_id"]: r for r in _load_sqlite_rows("holdings_13f")}
        brk = holdings["HLD_BRK_001"]
        assert brk["cusip"] == "037833100"
        assert brk["filer_cik"] == "0001067983"
        assert brk["shares"] == 300000000
        assert brk["market_value_usd"] == 69000000000.0
        assert encode_alg_a5_token(brk["cusip"]) == "9:037833100"
        assert compute_alg_a5_hash([brk["cusip"]], bkcc="GLOBAL") != compute_alg_a5_hash(
            ["37833100"], bkcc="GLOBAL"
        )


class TestWhitespaceCrlfAndNullSentinels:
    """BUG-04 CRLF->LF, asymmetric BK vs Sat casing, and '-1:^^' null/blank sentinel."""

    def test_crlf_and_cr_normalize_to_lf(self) -> None:
        crlf = "line1\r\nline2\rline3"
        lf = "line1\nline2\nline3"
        assert encode_alg_a5_token(crlf, is_business_key=False) == encode_alg_a5_token(
            lf, is_business_key=False
        )
        assert compute_alg_a5_hash([crlf], is_business_key=False) == compute_alg_a5_hash(
            [lf], is_business_key=False
        )

    def test_bk_uppercases_while_sat_preserves_case(self) -> None:
        assert encode_alg_a5_token("  aapl \r\n", is_business_key=True) == "4:AAPL"
        assert (
            compute_alg_a5_hash(["  aapl \r\n"], bkcc="global")
            == compute_alg_a5_hash(["AAPL"], bkcc="GLOBAL")
        )
        assert encode_alg_a5_token("  Apple Inc. \t", is_business_key=False) == "10:Apple Inc."
        assert (
            compute_alg_a5_hash(["  Apple Inc. \t"], is_business_key=False)
            != compute_alg_a5_hash(["apple inc."], is_business_key=False)
        )

    def test_null_blank_sentinel_and_literal_immunity(self) -> None:
        assert NULL_TOKEN == "-1:^^"
        for blank in (None, "", "   \t\r\n  "):
            assert encode_alg_a5_token(blank) == "-1:^^"

        assert encode_alg_a5_token("-1") == "2:-1"
        assert encode_alg_a5_token("-2") == "2:-2"
        assert encode_alg_a5_token("-1:^^") == "5:-1:^^"

        hashes = {
            compute_alg_a5_hash([None, "AAPL"], bkcc="GLOBAL"),
            compute_alg_a5_hash(["-1", "AAPL"], bkcc="GLOBAL"),
            compute_alg_a5_hash(["-2", "AAPL"], bkcc="GLOBAL"),
            compute_alg_a5_hash(["-1:^^", "AAPL"], bkcc="GLOBAL"),
        }
        assert len(hashes) == 4
        assert compute_alg_a5_hash(["-1:^^"], bkcc="GLOBAL") != ZERO_KEY_UNKNOWN

    def test_real_sqlite_securities_null_cusips_encode_as_null_token(self) -> None:
        secs = {r["ticker"]: r for r in _load_sqlite_rows("securities")}
        assert len(secs) == 62
        null_rows = [r for r in secs.values() if r["cusip"] is None]
        assert len(null_rows) == 42
        assert secs["NVDA"]["cusip"] is None
        assert secs["BRK-B"]["cusip"] is None
        assert encode_alg_a5_token(secs["NVDA"]["cusip"]) == "-1:^^"
        assert encode_alg_a5_token(secs["AAPL"]["cusip"]) == "9:037833100"
        assert encode_alg_a5_token(secs["AMZN"]["cusip"]) == "9:023135106"


class TestDelimiterImmunityAndAlphabeticalHashDiff:
    """Length-prefixing prevents '||' collisions; Satellite hash_diff sorts alphabetically."""

    def test_delimiter_collision_immunity(self) -> None:
        assert DEFAULT_DELIMITER == "||"
        assert compute_alg_a5_prehash(["AB", "C"], is_business_key=True) == "2:AB||1:C"
        assert compute_alg_a5_prehash(["A", "BC"], is_business_key=True) == "1:A||2:BC"
        assert compute_alg_a5_hash(["AB", "C"], bkcc="GLOBAL") != compute_alg_a5_hash(
            ["A", "BC"], bkcc="GLOBAL"
        )

        assert compute_alg_a5_prehash(["A||B", "C"], is_business_key=True) == "4:A||B||1:C"
        assert compute_alg_a5_prehash(["A", "B||C"], is_business_key=True) == "1:A||4:B||C"
        assert compute_alg_a5_hash(["A||B", "C"], bkcc="GLOBAL") != compute_alg_a5_hash(
            ["A", "B||C"], bkcc="GLOBAL"
        )

    def test_alphabetical_hash_diff_and_real_parquet_permutation_invariance(self) -> None:
        pre1 = compute_alg_a5_prehash(
            {"z": "1", "a": "2", "m": "3"}, is_business_key=False, sort_alphabetically=True
        )
        pre2 = compute_alg_a5_prehash(
            {"a": "2", "m": "3", "z": "1"}, is_business_key=False, sort_alphabetically=True
        )
        assert pre1 == "1:2||1:3||1:1"
        assert pre1 == pre2

        rows = {r["esg_rating_id"]: r for r in _load_parquet_rows()}
        aapl = rows["ESG_AAPL_MSCI_ESG_2024"]
        typed_map = {
            "esg_overall_grade": "UTF8_STRING",
            "rating_effective_date": "DATE32",
            "controversy_flag": "BOOLEAN",
            "voted_shares": "INT64",
            "carbon_intensity_tco2e_per_musd": DecimalType(precision=12, scale=4),
        }
        forward = {
            "esg_overall_grade": aapl["esg_overall_grade"],
            "rating_effective_date": aapl["rating_effective_date"],
            "controversy_flag": aapl["controversy_flag"],
            "voted_shares": aapl["voted_shares"],
            "carbon_intensity_tco2e_per_musd": Decimal(
                str(aapl["carbon_intensity_tco2e_per_musd"])
            ),
        }
        reversed_map = dict(reversed(list(forward.items())))
        assert compute_alg_a5_hash(
            forward, is_business_key=False, sort_alphabetically=True, column_types=typed_map
        ) == compute_alg_a5_hash(
            reversed_map, is_business_key=False, sort_alphabetically=True, column_types=typed_map
        )


class TestCanonicalScalarSerialization:
    """Canonical scalar rules for BOOLEAN, DATE32, TIMESTAMP_UTC_US, and DECIMAL."""

    def test_boolean_date32_timestamp_and_decimal(self) -> None:
        assert encode_alg_a5_token(True, logical_type="BOOLEAN") == "4:true"
        assert encode_alg_a5_token(False, logical_type="BOOLEAN") == "5:false"
        assert encode_alg_a5_token("true", logical_type="BOOLEAN") == "4:true"
        with pytest.raises(ValueError, match="Invalid BOOLEAN"):
            format_scalar_alg_a5("maybe", logical_type="BOOLEAN")

        assert encode_alg_a5_token(date(2024, 6, 15), logical_type="DATE32") == "10:2024-06-15"
        assert (
            encode_alg_a5_token("2024-05-01T10:00:00", logical_type="DATE32") == "10:2024-05-01"
        )

        tz_plus2 = timezone(timedelta(hours=2))
        dt_aware = datetime(2024, 5, 1, 14, 30, 0, 123456, tzinfo=tz_plus2)
        assert (
            encode_alg_a5_token(dt_aware, logical_type="TIMESTAMP_UTC_US")
            == "27:2024-05-01T12:30:00.123456Z"
        )
        dt_naive = datetime(2024, 5, 1, 12, 30, 0, 0)
        assert (
            encode_alg_a5_token(dt_naive, logical_type="TIMESTAMP_UTC_US")
            == "27:2024-05-01T12:30:00.000000Z"
        )
        assert (
            encode_alg_a5_token("2024-05-01T14:30:00.123456+02:00", logical_type="TIMESTAMP_UTC_US")
            == "27:2024-05-01T12:30:00.123456Z"
        )

        assert encode_alg_a5_token(Decimal("400.2200")) == "6:400.22"
        assert encode_alg_a5_token(Decimal("100.00")) == "3:100"
        assert encode_alg_a5_token(Decimal("-0.00")) == "1:0"
        assert encode_alg_a5_token(Decimal("14.20")) == "4:14.2"
        assert encode_alg_a5_token(Decimal("12.8000")) == "4:12.8"
        with pytest.raises(ValueError, match="Non-finite"):
            format_scalar_alg_a5(Decimal("NaN"))
        with pytest.raises(ValueError, match="Non-finite"):
            format_scalar_alg_a5(float("inf"))

    def test_38_digit_decimal_precision_preserved(self) -> None:
        int_38 = "12345678901234567890123456789012345678"
        frac_38 = "12345678901234567890.123456789012345678"
        tiny_delta = "1.000000000000000000000000000001"

        assert (
            format_scalar_alg_a5(Decimal(int_38), logical_type=DecimalType(precision=38, scale=0))
            == int_38
        )
        assert (
            format_scalar_alg_a5(
                Decimal(frac_38), logical_type=DecimalType(precision=38, scale=18)
            )
            == frac_38
        )
        assert format_scalar_alg_a5(Decimal(tiny_delta)) == tiny_delta
        assert compute_alg_a5_hash(
            [Decimal(tiny_delta)], bkcc="GLOBAL"
        ) != compute_alg_a5_hash([Decimal("1")], bkcc="GLOBAL")

    def test_blank_strings_across_all_typed_logical_types_and_all_blank_bk(self) -> None:
        typed_cases: list[Any] = [
            "BOOLEAN",
            "DATE32",
            "TIMESTAMP_UTC_US",
            DecimalType(precision=10, scale=2),
            "INT32",
            "INT64",
            "UTF8_STRING",
        ]
        for ltype in typed_cases:
            for blank in ("", "   ", "\t\r\n"):
                assert encode_alg_a5_token(blank, logical_type=ltype, is_business_key=True) == "-1:^^"
                assert encode_alg_a5_token(blank, logical_type=ltype, is_business_key=False) == "-1:^^"

        col_types: dict[str, Any] = {"d1": "DATE32", "d2": "TIMESTAMP_UTC_US"}
        assert (
            compute_alg_a5_hash({"d1": "   ", "d2": ""}, column_types=col_types, bkcc="GLOBAL")
            == ZERO_KEY_UNKNOWN
        )
        assert (
            compute_alg_a5_hash(
                {"d1": "   ", "d2": ""},
                column_types=col_types,
                bkcc="GLOBAL",
                is_optional_inapplicable=True,
            )
            == ZERO_KEY_ERROR
        )
        composite_hash = compute_alg_a5_hash(
            {"d1": "   ", "d2": "2024-05-01T12:30:00z"},
            column_types=col_types,
            bkcc="GLOBAL",
        )
        assert composite_hash == hashlib.sha256(
            b"6:GLOBAL||-1:^^||27:2024-05-01T12:30:00.000000Z"
        ).hexdigest()

    def test_explicit_temporal_logical_type_and_lowercase_z(self) -> None:
        tz_minus4 = timezone(timedelta(hours=-4))
        edt_cross_midnight = datetime(2024, 5, 1, 23, 30, 0, tzinfo=tz_minus4)
        assert encode_alg_a5_token(edt_cross_midnight, logical_type="DATE32") == "10:2024-05-02"
        assert encode_alg_a5_token(datetime(2024, 5, 1, 12, 0), logical_type="DATE32") == "10:2024-05-01"
        assert (
            encode_alg_a5_token(date(2024, 5, 1), logical_type="TIMESTAMP_UTC_US")
            == "27:2024-05-01T00:00:00.000000Z"
        )
        assert (
            encode_alg_a5_token("2024-05-01T12:30:00z", logical_type="TIMESTAMP_UTC_US")
            == "27:2024-05-01T12:30:00.000000Z"
        )


class TestBkccTiersAndHexCase:
    """3-Tier Key Authority BKCC scoping (BUG-08) and lowercase 64-hex digests (BUG-06)."""

    def test_three_tier_bkcc_resolution(self) -> None:
        assert resolve_bkcc("TIER_1_GLOBAL_STANDARD", "IGNORES_OVERRIDE") == "GLOBAL"
        assert resolve_bkcc("TIER_2_ENTERPRISE_SOR", "  sec_edgar \r\n") == "SEC_EDGAR"
        assert resolve_bkcc("TIER_2_ENTERPRISE_SOR", None) == "GLOBAL"
        assert resolve_bkcc("TIER_3_LOCAL_SCOPED", "ibor_sqlite") == "IBOR_SQLITE"
        with pytest.raises(ValueError, match="TIER_3_LOCAL_SCOPED"):
            resolve_bkcc("TIER_3_LOCAL_SCOPED", "   ")

    def test_lowercase_hex_and_bug08_tuple_scoping(self) -> None:
        hk_global = compute_alg_a5_hash(
            ["0001067983"], key_tier="TIER_1_GLOBAL_STANDARD", bkcc="IGNORED"
        )
        hk_local = compute_alg_a5_hash(
            ["0001067983"], key_tier="TIER_3_LOCAL_SCOPED", bkcc="SEC_13F"
        )
        assert re.fullmatch(r"[0-9a-f]{64}", hk_global)
        assert re.fullmatch(r"[0-9a-f]{64}", hk_local)
        assert hk_global != hk_local
        assert hk_global == hashlib.sha256(b"6:GLOBAL||10:0001067983").hexdigest()
        assert hk_local == hashlib.sha256(b"7:SEC_13F||10:0001067983").hexdigest()


class TestSentinelsPriorityAndSeedRecords:
    """Zero-Key -1/-2 short-circuit priority, SeedRecordsSpec defaults, and execute_hash_recipe."""

    def test_seed_records_spec_and_ghost_defaults(self) -> None:
        spec = SeedRecordsSpec()
        assert spec.unknown_hk_hex == "0" * 64
        assert spec.error_hk_hex == "f" * 64
        assert spec.ghost_hash_diff_hex == "0" * 64
        assert spec.ghost_load_dts == "1900-01-01T00:00:00Z"
        assert spec.ghost_rec_src == "SYSTEM_SEED"
        assert spec.default_string == "UNKNOWN"
        assert spec.default_num == -1
        assert spec.default_hash_input == "0"

        seeds = build_seed_records(spec)
        assert seeds["unknown"]["hk_hex"] == ZERO_KEY_UNKNOWN
        assert seeds["error"]["hk_hex"] == ZERO_KEY_ERROR
        assert seeds["ghost"]["load_dts"] == "1900-01-01T00:00:00Z"
        assert seeds["ghost"]["hash_diff_hex"] == "0" * 64

    def test_short_circuit_priority_and_recipe_execution(self) -> None:
        err_pred = PredicateLeaf(op="EQ", column="status", literal="CORRUPT")
        unk_pred = PredicateLeaf(op="EQ", column="status", literal="CORRUPT")
        assert (
            compute_alg_a5_hash(
                {"cik": "0001067983"},
                row={"cik": "0001067983", "status": "CORRUPT"},
                bkcc="GLOBAL",
                error_predicate=err_pred,
                unknown_predicate=unk_pred,
            )
            == ZERO_KEY_ERROR
        )
        assert (
            compute_alg_a5_hash(
                {"cik": "0001067983"},
                row={"cik": "0001067983", "status": "UNKNOWN"},
                bkcc="GLOBAL",
                unknown_predicate=PredicateLeaf(op="EQ", column="status", literal="UNKNOWN"),
            )
            == ZERO_KEY_UNKNOWN
        )
        assert compute_alg_a5_hash(["-2"], bkcc="GLOBAL") == ZERO_KEY_ERROR
        assert compute_alg_a5_hash(["-1"], bkcc="GLOBAL") == ZERO_KEY_UNKNOWN
        assert compute_alg_a5_hash([None, "  "], bkcc="GLOBAL") == ZERO_KEY_UNKNOWN
        assert (
            compute_alg_a5_hash([None, "  "], bkcc="GLOBAL", is_optional_inapplicable=True)
            == ZERO_KEY_ERROR
        )

        recipe = HashRecipeSpec(
            target_col="hk_issuer",
            recipe_kind="HUB_HK",
            bkcc_literal="GLOBAL",
            source_columns=["cik"],
            key_columns=["cik"],
            sort_alphabetically=False,
        )
        hk = execute_hash_recipe(
            recipe, {"cik": "0000320193"}, key_tier="TIER_1_GLOBAL_STANDARD"
        )
        assert hk == hashlib.sha256(b"6:GLOBAL||10:0000320193").hexdigest()

    def test_satellite_payload_minus_one_and_two_do_not_short_circuit(self) -> None:
        expected_minus_one = hashlib.sha256(b"2:-1").hexdigest()
        expected_minus_two = hashlib.sha256(b"2:-2").hexdigest()

        assert compute_alg_a5_hash(["-1"], is_business_key=False) == expected_minus_one
        assert compute_alg_a5_hash([-1], is_business_key=False) == expected_minus_one
        assert compute_alg_a5_hash(["-1"], is_business_key=False) != ZERO_KEY_UNKNOWN

        assert compute_alg_a5_hash(["-2"], is_business_key=False) == expected_minus_two
        assert compute_alg_a5_hash([-2], is_business_key=False) == expected_minus_two
        assert compute_alg_a5_hash(["-2"], is_business_key=False) != ZERO_KEY_ERROR

        # Business keys preserve -1/-2 sentinel short-circuiting.
        assert compute_alg_a5_hash(["-1"], is_business_key=True) == ZERO_KEY_UNKNOWN
        assert compute_alg_a5_hash(["-2"], is_business_key=True) == ZERO_KEY_ERROR

        # Explicit error/unknown predicates still trigger for Satellite payloads when matched.
        assert (
            compute_alg_a5_hash(
                {"delta_code": "-1"},
                is_business_key=False,
                unknown_predicate=PredicateLeaf(op="EQ", column="flag", literal="UNK"),
                row={"delta_code": "-1", "flag": "UNK"},
            )
            == ZERO_KEY_UNKNOWN
        )
        assert (
            compute_alg_a5_hash(
                {"delta_code": "-2"},
                is_business_key=False,
                error_predicate=PredicateLeaf(op="EQ", column="flag", literal="ERR"),
                row={"delta_code": "-2", "flag": "ERR"},
            )
            == ZERO_KEY_ERROR
        )

        # execute_hash_recipe propagates is_business_key=False for SAT_HASH_DIFF vs True for HUB_HK.
        sat_recipe = HashRecipeSpec(
            target_col="hash_diff",
            recipe_kind="SAT_HASH_DIFF",
            source_columns=["sentinel_code"],
            sort_alphabetically=True,
        )
        assert execute_hash_recipe(sat_recipe, {"sentinel_code": -1}) == expected_minus_one
        assert execute_hash_recipe(sat_recipe, {"sentinel_code": "-2"}) == expected_minus_two

        hub_recipe = HashRecipeSpec(
            target_col="hk_issuer",
            recipe_kind="HUB_HK",
            bkcc_literal="GLOBAL",
            source_columns=["cik"],
            key_columns=["cik"],
            sort_alphabetically=False,
        )
        assert execute_hash_recipe(hub_recipe, {"cik": "-1"}) == ZERO_KEY_UNKNOWN
        assert execute_hash_recipe(hub_recipe, {"cik": "-2"}) == ZERO_KEY_ERROR


class TestPredicateNodeRealDataEvaluation:
    """PredicateNode AST evaluation on real SQLite & Parquet Asset Management datasets."""

    def test_sqlite_securities_is_not_null_filter_bug02(self) -> None:
        rows = _load_sqlite_rows("securities")
        pred = PredicateLeaf(op="IS_NOT_NULL", column="cusip")
        passed = [r for r in rows if evaluate_predicate(pred, r)]
        rejected = [r for r in rows if not evaluate_predicate(pred, r)]
        assert len(passed) == 20
        assert len(rejected) == 42
        by_ticker = {r["ticker"]: r for r in rows}
        assert evaluate_predicate(pred, by_ticker["AAPL"]) is True
        assert evaluate_predicate(pred, by_ticker["AMZN"]) is True
        assert evaluate_predicate(pred, by_ticker["NVDA"]) is False
        assert evaluate_predicate(pred, by_ticker["BRK-B"]) is False

    def test_parquet_hard_breach_and_climate_ai_filters(self) -> None:
        rows = _load_parquet_rows()
        assert len(rows) == 30

        hard_breach_pred = PredicateBranch(
            op="AND",
            children=[
                PredicateLeaf(
                    op="EQ", column="mandate_policy_name", literal="PARIS_ALIGNED_BENCHMARK"
                ),
                PredicateLeaf(op="EQ", column="breach_severity", literal="HARD_BREACH"),
            ],
        )
        breaches = [r for r in rows if evaluate_predicate(hard_breach_pred, r)]
        assert len(breaches) == 2
        assert {r["ticker"]: r["carbon_intensity_tco2e_per_musd"] for r in breaches} == {
            "CVX": 400.22,
            "OXY": 470.74,
        }

        topic_pred = PredicateBranch(
            op="OR",
            children=[
                PredicateLeaf(op="EQ", column="proposal_category", literal="CLIMATE_TRANSITION"),
                PredicateLeaf(op="EQ", column="proposal_category", literal="AI_GOVERNANCE"),
            ],
        )
        opposing_topic_rows = [
            r
            for r in rows
            if evaluate_predicate(topic_pred, r)
            and r["vote_cast"] != r["management_recommendation"]
        ]
        assert len(opposing_topic_rows) == 14

    def test_predicate_bool_int_separation(self) -> None:
        assert evaluate_predicate(PredicateLeaf(op="EQ", column="flag", literal=1), {"flag": True}) is False
        assert evaluate_predicate(PredicateLeaf(op="EQ", column="flag", literal=True), {"flag": 1}) is False


class TestBug01To08IROracle:
    """Explicit BUG-01..08 specification assertions across ir_kernel and Vault IR models."""

    def test_bug_01_to_08_invariants(self) -> None:
        # BUG-01: UTF-8 byte length prefixing
        assert encode_alg_a5_token("Nordisk Suverän Fond", is_business_key=False).startswith("21:")

        # BUG-02: Sparse FK filtering via LoadSemanticsSpec.filter_ast
        load_sem = LoadSemanticsSpec(
            source_table_id="sqlite.securities",
            filter_ast=PredicateLeaf(op="IS_NOT_NULL", column="cusip"),
        )
        assert evaluate_predicate(load_sem.filter_ast, {"cusip": None}) is False  # type: ignore[arg-type]

        # BUG-03: Approved unstructured extraction review gate
        unstruct_sem = LoadSemanticsSpec(
            source_table_id="doc.am_stewardship_domain_notes",
            require_review_status="APPROVED",
        )
        assert unstruct_sem.require_review_status == "APPROVED"

        # BUG-04: CRLF normalization
        assert encode_alg_a5_token("a\r\nb", is_business_key=False) == "3:a\nb"

        # BUG-05: Bitemporal satellite tracking applied_dts_col
        bitemp_sat = DraftSatelliteSpec(
            sat_name="sat_esg_rating_bitemporal",
            sat_kind="STANDARD",
            parent_name="hub_esg_rating",
            is_bitemporal=True,
            applied_dts_col="rating_effective_date",
            attributes=[
                DraftField(
                    name="esg_overall_grade",
                    logical_type="UTF8_STRING",
                    nullable=False,
                    is_pii=False,
                )
            ],
        )
        assert bitemp_sat.is_bitemporal is True
        assert bitemp_sat.applied_dts_col == "rating_effective_date"

        # BUG-06: Lowercase 64-hex SHA-256
        digest = compute_alg_a5_hash(["0001067983"], bkcc="GLOBAL")
        assert digest == digest.lower() and len(digest) == 64

        # BUG-07: Zero-rehash EXT companion satellite + SchemaWaveDelta
        ext_sat = CertifiedSatelliteSpec(
            sat_name="sat_compliance_breach_alert_ext_v2",
            sat_kind="EXT",
            parent_name="hub_compliance_breach_alert",
            source_id="src_esg",
            source_table_id="parquet.am_esg_stewardship_proxy",
            extends_sat_name="sat_compliance_breach_alert",
            companion_epoch=2,
            source_bindings=[
                SourceBindingSpec(
                    source_id="src_esg",
                    table_id="parquet.am_esg_stewardship_proxy",
                    col_mappings={"waiver_expiry_date": "waiver_expiry_date"},
                )
            ],
            columns=[
                IRField(
                    field_id=1,
                    name="hk_compliance_breach_alert",
                    logical_type="FIXED_BINARY_32",
                    nullable=False,
                    is_pii=False,
                ),
                IRField(
                    field_id=2,
                    name="waiver_expiry_date",
                    logical_type="DATE32",
                    nullable=True,
                    is_pii=False,
                ),
            ],
            physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_compliance_breach_alert"]),
        )
        wave_ir = CertifiedVaultIR(
            project_id="am-ibor-2026",
            ontology_version="1.1.0",
            wave_number=2,
            satellites=[ext_sat],
            wave_delta=SchemaWaveDelta(
                from_wave=1,
                to_wave=2,
                added_entities=["sat_compliance_breach_alert_ext_v2"],
                added_sat_extensions=["sat_compliance_breach_alert_ext_v2"],
                locked_entities=["hub_compliance_breach_alert"],
            ),
        )
        assert wave_ir.wave_delta is not None
        assert "sat_compliance_breach_alert_ext_v2" in wave_ir.wave_delta.added_sat_extensions

        # BUG-08: Per-(table_id, col_name, target_hub) BKCC tuple scoping
        part_filer = BusinessKeyPart(
            table_id="sqlite.institutional_filers",
            col_name="filer_cik",
            source_id="src_sqlite",
            key_tier="TIER_3_LOCAL_SCOPED",
            bkcc_value="SEC_13F",
        )
        part_issuer = BusinessKeyPart(
            table_id="sqlite.issuers",
            col_name="cik",
            source_id="src_sqlite",
            key_tier="TIER_1_GLOBAL_STANDARD",
            bkcc_value="GLOBAL",
        )
        assert (part_filer.table_id, part_filer.col_name, part_filer.bkcc_value) != (
            part_issuer.table_id,
            part_issuer.col_name,
            part_issuer.bkcc_value,
        )
        draft = DraftVaultIR(
            project_id="am-ibor-2026",
            ontology_version="1.0.0",
            wave_number=1,
            satellites=[
                DraftSatelliteSpec(
                    sat_name="sat_issuer_core",
                    sat_kind="STANDARD",
                    parent_name="hub_issuer",
                    source_bindings=[
                        DraftSourceBinding(
                            source_id="src_sqlite",
                            table_id="sqlite.issuers",
                            col_mappings={"company_name": "company_name"},
                        )
                    ],
                )
            ],
        )
        assert draft.ir_version == "2.0"
        assert datetime.now(UTC).year >= 2024
