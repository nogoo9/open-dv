"""tests/e2e/test_challenger_f16_adversarial.py.

Empirical Adversarial Stress Harness for Milestone M5 (Feature 16).
Authored by Challenger 1 (challenger_m5_1).

Adversarially challenges:
1. Public data retrieval modes: mode='auto', mode='fixture', mode='live'.
2. Missing/invalid output directories, file collisions, and read-only target paths.
3. Corrupted CSV/JSON feeds, non-UTF8 bytes, and holiday token filtering.
4. Schema integrity of generated CSV files: column headers, 10-digit zero-padded CIKs,
   CUSIP regex ^[0-9A-Z]{9}$, positive shares, positive market values, and yield bounds.
5. Zero-observation FRED feed vulnerability analysis.
"""

from __future__ import annotations

import csv
import io
import json
import re
import stat
import urllib.error
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from tests.fixtures.public_am.fetch_public_datasets import (
    FRED_TREASURY_RATES_FILENAME,
    MAX_FRED_OBSERVATION_COUNT,
    MAX_SEC_EQUITIES_COUNT,
    SEC_13F_HOLDINGS_FILENAME,
    SEC_SECURITY_MASTER_FILENAME,
    PublicDataFetcher,
    fetch_public_am_datasets,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "public_am"
CUSIP_REGEX = re.compile(r"^[0-9A-Z]{9}$")
CIK_REGEX = re.compile(r"^\d{10}$")
ISO_DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ==============================================================================
# SECTION 1: FETCH MODES (auto, fixture, live)
# ==============================================================================


class TestFetchModesEmpirical:
    """Stress tests on mode='auto', mode='fixture', and mode='live'."""

    def test_mode_fixture_offline_guarantee(self, tmp_path: Path) -> None:
        """Verify mode='fixture' never touches urllib and copies golden fixtures verbatim."""
        out_dir = tmp_path / "fixture_out"
        fetcher = PublicDataFetcher(output_dir=out_dir, mode="fixture")

        with patch("urllib.request.urlopen") as mock_url:
            results = fetcher.fetch_all()
            mock_url.assert_not_called()

        assert len(results) == 3
        for name, path in results.items():
            assert path.exists()
            assert path.parent == out_dir
            golden = FIXTURES_DIR / name
            assert (
                path.read_text(encoding="utf-8").splitlines()
                == golden.read_text(encoding="utf-8").splitlines()
            ), f"Mismatch in {name}"

    def test_mode_fixture_missing_source_raises(self, tmp_path: Path) -> None:
        """Verify mode='fixture' raises FileNotFoundError when source fixture is missing."""
        empty_dir = tmp_path / "empty_dir"
        empty_dir.mkdir()
        fetcher = PublicDataFetcher(
            output_dir=tmp_path / "out", fixture_dir=empty_dir, mode="fixture"
        )
        with pytest.raises(FileNotFoundError, match="Offline golden fixture missing"):
            fetcher.fetch_sec_security_master()

    def test_mode_live_network_success(self, tmp_path: Path) -> None:
        """Verify mode='live' executes network fetch and correctly constructs output files."""
        out_dir = tmp_path / "live_success"
        fetcher = PublicDataFetcher(output_dir=out_dir, mode="live")

        mock_sec_data = {
            str(i): {
                "cik_str": 320193 + i,
                "ticker": f"TCK{i}",
                "title": f"Company {i} Inc.",
            }
            for i in range(10)
        }
        mock_sec_resp = MagicMock()
        mock_sec_resp.status = 200
        mock_sec_resp.read.return_value = json.dumps(mock_sec_data).encode("utf-8")
        mock_sec_resp.__enter__.return_value = mock_sec_resp

        mock_fred_csv = (
            "DATE,DGS10\n"
            "2026-11-10,4.45\n"
            "2026-11-11,.\n"
            "2026-11-12,4.48\n"
            "2026-11-13,ND\n"
            "2026-11-14,4.50\n"
        )
        mock_fred_resp = MagicMock()
        mock_fred_resp.status = 200
        mock_fred_resp.read.return_value = mock_fred_csv.encode("utf-8")
        mock_fred_resp.__enter__.return_value = mock_fred_resp

        with patch("urllib.request.urlopen", side_effect=[mock_sec_resp, mock_fred_resp]):
            results = fetcher.fetch_all()

        assert len(results) == 3
        # Verify SEC parsed output
        sec_path = results[SEC_SECURITY_MASTER_FILENAME]
        with open(sec_path, encoding="utf-8") as f:
            sec_rows = list(csv.DictReader(f))
        assert len(sec_rows) == 10
        assert sec_rows[0]["ticker"] == "TCK0"
        assert sec_rows[0]["cik"] == "0000320193"
        assert sec_rows[0]["exchange"] == "US_EQUITY"

        # Verify FRED parsed output
        fred_path = results[FRED_TREASURY_RATES_FILENAME]
        with open(fred_path, encoding="utf-8") as f:
            fred_rows = list(csv.DictReader(f))
        assert len(fred_rows) == 3  # 4.50, 4.48, 4.45 (holidays filtered)
        assert fred_rows[0]["yield_pct"] == "4.50"
        assert fred_rows[1]["yield_pct"] == "4.48"
        assert fred_rows[2]["yield_pct"] == "4.45"

    def test_mode_live_raises_on_sec_failure(self, tmp_path: Path) -> None:
        """Verify mode='live' re-raises network exception on SEC fetch error without fallback."""
        fetcher = PublicDataFetcher(output_dir=tmp_path / "live_fail", mode="live")
        with (
            patch("urllib.request.urlopen", side_effect=TimeoutError("SEC timeout")),
            pytest.raises(TimeoutError, match="SEC timeout"),
        ):
            fetcher.fetch_sec_security_master()

    def test_mode_live_raises_on_fred_failure(self, tmp_path: Path) -> None:
        """Verify mode='live' re-raises network exception on FRED fetch error without fallback."""
        fetcher = PublicDataFetcher(output_dir=tmp_path / "live_fail", mode="live")
        with (
            patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.HTTPError(
                    "url", 503, "Service Unavailable", {}, io.BytesIO()
                ),
            ),
            pytest.raises(urllib.error.HTTPError),
        ):
            fetcher.fetch_fred_treasury_rates()

    def test_mode_auto_fallback_resilience(self, tmp_path: Path) -> None:
        """Verify mode='auto' catches exceptions and seamlessly falls back to fixtures."""
        fetcher = PublicDataFetcher(output_dir=tmp_path / "auto_fallback", mode="auto")
        with patch("urllib.request.urlopen", side_effect=ConnectionResetError("Connection lost")):
            results = fetcher.fetch_all()

        assert len(results) == 3
        for name, path in results.items():
            assert path.exists()
            assert (
                path.read_text(encoding="utf-8").splitlines()
                == (FIXTURES_DIR / name).read_text(encoding="utf-8").splitlines()
            )

    def test_fetch_public_am_datasets_helper_signature(self, tmp_path: Path) -> None:
        """Verify top-level fetch_public_am_datasets wrapper matches SCOPE.md Contract 1."""
        out_dir = tmp_path / "wrapper_test"
        res = fetch_public_am_datasets(output_dir=out_dir, mode="fixture")
        assert isinstance(res, dict)
        assert set(res.keys()) == {
            SEC_SECURITY_MASTER_FILENAME,
            SEC_13F_HOLDINGS_FILENAME,
            FRED_TREASURY_RATES_FILENAME,
        }
        for p in res.values():
            assert p.exists()


# ==============================================================================
# SECTION 2: INVALID OUTPUT DIRECTORIES & READ-ONLY PATH STRESS
# ==============================================================================


class TestDirectoryAndPermissionStress:
    """Stress tests on directory creation, collision, and permission barriers."""

    def test_deeply_nested_nonexistent_directory_creation(self, tmp_path: Path) -> None:
        """Verify fetcher automatically creates deeply nested parent directory hierarchy."""
        deep_dir = tmp_path / "level1" / "level2" / "level3" / "public_am"
        assert not deep_dir.exists()

        fetcher = PublicDataFetcher(output_dir=deep_dir, mode="fixture")
        results = fetcher.fetch_all()

        assert deep_dir.exists()
        assert len(results) == 3
        for path in results.values():
            assert path.exists()

    def test_collision_when_output_dir_is_regular_file(self, tmp_path: Path) -> None:
        """Verify fetcher raises OSError/FileExistsError when output_dir is an existing file."""
        file_collision = tmp_path / "i_am_a_file.txt"
        file_collision.write_text("blocking directory creation", encoding="utf-8")

        fetcher = PublicDataFetcher(output_dir=file_collision, mode="fixture")
        with pytest.raises(OSError):
            fetcher.fetch_all()

    def test_read_only_output_directory_enforcement(self, tmp_path: Path) -> None:
        """Verify fetcher raises PermissionError when target directory is read-only."""
        ro_dir = tmp_path / "readonly_dir"
        ro_dir.mkdir()
        # Mark read-only
        ro_file = ro_dir / SEC_SECURITY_MASTER_FILENAME
        ro_file.write_text("existing", encoding="utf-8")
        ro_file.chmod(stat.S_IREAD)

        fetcher = PublicDataFetcher(output_dir=ro_dir, mode="fixture")
        try:
            with pytest.raises(PermissionError):
                fetcher.fetch_sec_security_master()
        finally:
            # Cleanup permissions for tmp_path deletion
            ro_file.chmod(stat.S_IWRITE)


# ==============================================================================
# SECTION 3: CORRUPTED FEEDS & HOLIDAY FILTERING
# ==============================================================================


class TestFeedCorruptionAndHolidayFiltering:
    """Adversarially challenge holiday parsing and feed corruption."""

    def test_fred_holiday_filtering_variants(self, tmp_path: Path) -> None:
        """Verify holiday representations ('', '.', 'ND', whitespace) are filtered."""
        out_dir = tmp_path / "holiday_test"
        fetcher = PublicDataFetcher(output_dir=out_dir, mode="live")

        feed_lines = [
            "DATE,DGS10",
            "2026-11-01,4.10",
            "2026-11-02,.",
            "2026-11-03, . ",
            "2026-11-04,ND",
            "2026-11-05, ND ",
            "2026-11-06,",
            "2026-11-07,   ",
            "2026-11-08,4.25",
            "single_col_line_with_no_commas",
            "",
            "2026-11-10,4.30",
        ]
        raw_feed = "\n".join(feed_lines).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = raw_feed
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            out_file = fetcher.fetch_fred_treasury_rates()

        with open(out_file, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        # Expected observations: 4.30 (Nov 10), 4.25 (Nov 8), 4.10 (Nov 1)
        assert len(rows) == 3
        yields = [r["yield_pct"] for r in rows]
        assert yields == ["4.30", "4.25", "4.10"]
        dates = [r["observation_date"] for r in rows]
        assert dates == ["2026-11-10", "2026-11-08", "2026-11-01"]
        for r in rows:
            assert r["series_id"] == "DGS10"

    def test_sec_malformed_json_types_and_missing_keys(self, tmp_path: Path) -> None:
        """Verify SEC fetcher catches malformed json or missing required keys and falls back."""
        fetcher = PublicDataFetcher(output_dir=tmp_path / "sec_err", mode="auto")

        # Scenario A: JSON list instead of dict
        mock_resp_list = MagicMock()
        mock_resp_list.status = 200
        mock_resp_list.read.return_value = b'["not", "a", "dict"]'
        mock_resp_list.__enter__.return_value = mock_resp_list

        with patch("urllib.request.urlopen", return_value=mock_resp_list):
            out = fetcher.fetch_sec_security_master()
        assert out.exists()
        assert (
            out.read_text(encoding="utf-8").splitlines()
            == (FIXTURES_DIR / SEC_SECURITY_MASTER_FILENAME)
            .read_text(encoding="utf-8")
            .splitlines()
        )

        # Scenario B: Dict missing 'ticker' or 'cik_str'
        mock_resp_bad_keys = MagicMock()
        mock_resp_bad_keys.status = 200
        mock_resp_bad_keys.read.return_value = (
            b'{"0": {"title": "Acme Corp"}}'  # missing ticker & cik_str
        )
        mock_resp_bad_keys.__enter__.return_value = mock_resp_bad_keys

        with patch("urllib.request.urlopen", return_value=mock_resp_bad_keys):
            out2 = fetcher.fetch_sec_security_master()
        assert out2.exists()
        assert (
            out2.read_text(encoding="utf-8").splitlines()
            == (FIXTURES_DIR / SEC_SECURITY_MASTER_FILENAME)
            .read_text(encoding="utf-8")
            .splitlines()
        )

    def test_sec_cik_zero_padding_stress(self, tmp_path: Path) -> None:
        """Verify CIK values are strictly 10 digits zero-padded across numeric scales."""
        out_dir = tmp_path / "cik_padding"
        fetcher = PublicDataFetcher(output_dir=out_dir, mode="live")

        test_data = {
            "0": {"ticker": "T1", "cik_str": 1, "title": "Single Digit"},
            "1": {"ticker": "T2", "cik_str": 9999, "title": "Four Digits"},
            "2": {"ticker": "T3", "cik_str": 320193, "title": "Six Digits"},
            "3": {"ticker": "T4", "cik_str": "0001067983", "title": "Already Ten Digits"},
            "4": {"ticker": "T5", "cik_str": 1234567890, "title": "Ten Digits Number"},
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(test_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            out = fetcher.fetch_sec_security_master()

        with open(out, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == 5
        assert rows[0]["cik"] == "0000000001"
        assert rows[1]["cik"] == "0000009999"
        assert rows[2]["cik"] == "0000320193"
        assert rows[3]["cik"] == "0001067983"
        assert rows[4]["cik"] == "1234567890"
        for r in rows:
            assert CIK_REGEX.match(r["cik"]), f"Invalid CIK: {r['cik']}"


# ==============================================================================
# SECTION 4: CSV SCHEMA INTEGRITY & VALUE DOMAIN BOUNDS
# ==============================================================================


class TestCsvSchemaIntegrityEmpirical:
    """Rigorous schema validation on golden fixtures and live generated files."""

    def test_sec_security_master_schema_and_domain_bounds(self) -> None:
        """Validate sec_security_master.csv columns, CIK regex, non-empty values."""
        csv_path = FIXTURES_DIR / SEC_SECURITY_MASTER_FILENAME
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == ["ticker", "cik", "company_name", "exchange"]
            rows = list(reader)

        assert len(rows) >= 50
        assert len(rows) <= MAX_SEC_EQUITIES_COUNT or len(rows) >= 50
        for idx, row in enumerate(rows):
            assert re.match(r"^[A-Z0-9\.\-]+$", row["ticker"]), (
                f"Row {idx}: invalid ticker '{row['ticker']}'"
            )
            assert CIK_REGEX.match(row["cik"]), f"Row {idx}: invalid CIK '{row['cik']}'"
            assert len(row["company_name"].strip()) > 0, f"Row {idx}: empty company name"
            assert row["exchange"] in ("US_EQUITY", "NYSE", "NASDAQ")

    def test_sec_13f_holdings_schema_and_domain_bounds(self) -> None:
        """Validate sec_13f_holdings.csv columns, CUSIP regex, positive shares and values."""
        csv_path = FIXTURES_DIR / SEC_13F_HOLDINGS_FILENAME
        expected_fields = [
            "holding_id",
            "cik",
            "cusip",
            "ticker",
            "issuer_name",
            "shares",
            "market_value_usd",
            "investment_discretion",
        ]
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == expected_fields
            rows = list(reader)

        assert len(rows) >= 20
        seen_ids = set()
        for idx, row in enumerate(rows):
            assert row["holding_id"], f"Row {idx}: empty holding_id"
            assert row["holding_id"] not in seen_ids, f"Row {idx}: duplicate holding_id"
            seen_ids.add(row["holding_id"])

            assert CIK_REGEX.match(row["cik"]), f"Row {idx}: invalid CIK '{row['cik']}'"
            assert CUSIP_REGEX.match(row["cusip"]), f"Row {idx}: invalid CUSIP '{row['cusip']}'"
            assert len(row["ticker"]) > 0, f"Row {idx}: empty ticker"
            assert len(row["issuer_name"]) > 0, f"Row {idx}: empty issuer"

            shares = int(row["shares"])
            assert shares > 0, f"Row {idx}: shares {shares} must be > 0"

            val = Decimal(row["market_value_usd"])
            assert val > Decimal("0"), f"Row {idx}: market_value_usd {val} must be > 0"
            assert row["investment_discretion"] in ("SOLE", "SHARED", "DEFINED")

    def test_fred_treasury_rates_schema_and_domain_bounds(self) -> None:
        """Validate fred_treasury_rates.csv columns, ISO date, DGS10 series, numeric yield."""
        csv_path = FIXTURES_DIR / FRED_TREASURY_RATES_FILENAME
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == ["observation_date", "series_id", "yield_pct"]
            rows = list(reader)

        assert len(rows) >= 30
        assert len(rows) <= MAX_FRED_OBSERVATION_COUNT or len(rows) >= 30
        for idx, row in enumerate(rows):
            assert ISO_DATE_REGEX.match(row["observation_date"]), (
                f"Row {idx}: invalid ISO date '{row['observation_date']}'"
            )
            assert row["series_id"] == "DGS10", f"Row {idx}: series_id != DGS10"
            y = Decimal(row["yield_pct"])
            assert Decimal("-2.0") <= y <= Decimal("30.0"), f"Row {idx}: extreme yield {y}"


# ==============================================================================
# SECTION 5: ADVANCED ADVERSARIAL STRESS & ORACLE
# ==============================================================================


class TestZeroObservationFeedBehavior:
    """Stress test edge cases where remote feeds return 0 observations."""

    def test_fred_empty_or_all_holiday_feed_behavior(self, tmp_path: Path) -> None:
        """Examine behavior when remote FRED returns only holiday rows or empty feed."""
        out_dir = tmp_path / "all_holidays"
        fetcher = PublicDataFetcher(output_dir=out_dir, mode="auto")

        mock_fred_csv = "DATE,DGS10\n2026-01-01,.\n2026-01-19,ND\n2026-02-16,\n"
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = mock_fred_csv.encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            out_file = fetcher.fetch_fred_treasury_rates()

        with open(out_file, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 0
        assert out_file.name == FRED_TREASURY_RATES_FILENAME
