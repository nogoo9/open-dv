"""tests/e2e/test_challenger_e2e_stress.py.

Empirical Adversarial Stress Suite, Fuzzers, and Oracles.
Authored by teamwork_challenger (challenger_e2e_1) to stress-test:
1. Public dataset fixtures (tests/fixtures/public_am/) and fetch_public_datasets.py.
2. DuckDB shadow sandbox 4-zone DDL compilation, PK uniqueness, zero FK orphans, and zero-key.
3. Boundary mutations, data corruption, and error contract enforcement.
"""

from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import duckdb
import pytest
from tests.e2e.test_browser_pipeline_builder import CANONICAL_FOUR_ZONE_DDL
from tests.fixtures.public_am.fetch_public_datasets import (
    FRED_TREASURY_RATES_FILENAME,
    SEC_13F_HOLDINGS_FILENAME,
    SEC_SECURITY_MASTER_FILENAME,
    PublicDataFetcher,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "public_am"


# ==============================================================================
# SECTION 1: EMPIRICAL TESTS ON PUBLIC DATASET FIXTURES & FETCHER
# ==============================================================================


class TestPublicDatasetFixturesEmpirical:
    """Empirical verification of golden CSV fixtures format and integrity."""

    def test_sec_security_master_fixture_integrity(self) -> None:
        """Verify sec_security_master.csv columns, row counts, and data invariants."""
        csv_path = FIXTURES_DIR / SEC_SECURITY_MASTER_FILENAME
        assert csv_path.exists(), f"Golden fixture missing: {csv_path}"

        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert reader.fieldnames == ["ticker", "cik", "company_name", "exchange"]
        assert len(rows) >= 50, f"Expected >= 50 rows in security master, got {len(rows)}"

        seen_tickers = set()
        for idx, row in enumerate(rows):
            ticker = row["ticker"].strip()
            cik = row["cik"].strip()
            company = row["company_name"].strip()
            exchange = row["exchange"].strip()

            assert ticker, f"Row {idx}: empty ticker"
            assert len(cik) == 10 and cik.isdigit(), (
                f"Row {idx}: invalid CIK '{cik}', must be 10 digits"
            )
            assert company, f"Row {idx}: empty company name"
            assert exchange in ("US_EQUITY", "NYSE", "NASDAQ"), (
                f"Row {idx}: unexpected exchange '{exchange}'"
            )
            seen_tickers.add(ticker)

        # Ensure ticker uniqueness in security master
        assert len(seen_tickers) == len(rows), (
            "Duplicate tickers detected in sec_security_master.csv"
        )

    def test_sec_13f_holdings_fixture_integrity(self) -> None:
        """Verify sec_13f_holdings.csv schema, positive shares, and numerical ranges."""
        csv_path = FIXTURES_DIR / SEC_13F_HOLDINGS_FILENAME
        assert csv_path.exists(), f"Golden fixture missing: {csv_path}"

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
            rows = list(reader)

        assert reader.fieldnames == expected_fields
        assert len(rows) >= 20, f"Expected >= 20 holdings, got {len(rows)}"

        seen_holding_ids = set()
        for idx, row in enumerate(rows):
            holding_id = row["holding_id"].strip()
            cik = row["cik"].strip()
            cusip = row["cusip"].strip()
            shares = int(row["shares"])
            market_val = Decimal(row["market_value_usd"])

            assert holding_id, f"Row {idx}: empty holding_id"
            assert len(cik) == 10 and cik.isdigit(), f"Row {idx}: invalid CIK '{cik}'"
            assert len(cusip) == 9, f"Row {idx}: invalid CUSIP length '{cusip}'"
            assert shares > 0, f"Row {idx}: non-positive share count {shares}"
            assert market_val > Decimal("0"), f"Row {idx}: non-positive market value {market_val}"
            seen_holding_ids.add(holding_id)

        assert len(seen_holding_ids) == len(rows), (
            "Duplicate holding_ids in sec_13f_holdings.csv"
        )

    def test_fred_treasury_rates_fixture_integrity(self) -> None:
        """Verify fred_treasury_rates.csv dates, series identifier, and yield bounds."""
        csv_path = FIXTURES_DIR / FRED_TREASURY_RATES_FILENAME
        assert csv_path.exists(), f"Golden fixture missing: {csv_path}"

        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert reader.fieldnames == ["observation_date", "series_id", "yield_pct"]
        assert len(rows) >= 30, f"Expected >= 30 yield observations, got {len(rows)}"

        for idx, row in enumerate(rows):
            obs_date = row["observation_date"].strip()
            series_id = row["series_id"].strip()
            yield_pct = Decimal(row["yield_pct"])

            assert len(obs_date) == 10 and obs_date[4] == "-" and obs_date[7] == "-", (
                f"Row {idx}: invalid ISO date '{obs_date}'"
            )
            assert series_id == "DGS10", f"Row {idx}: unexpected series '{series_id}'"
            # Yield percentage sanity range (-1.0% to 25.0%)
            assert Decimal("-1.0") <= yield_pct <= Decimal("25.0"), (
                f"Row {idx}: yield_pct '{yield_pct}' outside realistic bounds"
            )


class TestPublicDataFetcherAdversarial:
    """Stress-test PublicDataFetcher with simulated network failures and corrupted payloads."""

    def test_fetcher_timeout_resilience_in_auto_mode(self, tmp_path: Path) -> None:
        """Assert fetcher falls back cleanly to offline fixtures when requests time out."""
        fetcher = PublicDataFetcher(output_dir=tmp_path / "out", mode="auto")

        with patch("urllib.request.urlopen", side_effect=TimeoutError("Connection timed out")):
            results = fetcher.fetch_all()

        assert len(results) == 3
        for filename, path in results.items():
            assert path.exists(), f"Expected fallback fixture for {filename}"
            assert path.stat().st_size > 0

    def test_fetcher_http_error_resilience_in_auto_mode(self, tmp_path: Path) -> None:
        """Assert fetcher falls back to fixture on HTTP 403 / 429 / 500 errors."""
        fetcher = PublicDataFetcher(output_dir=tmp_path / "out", mode="auto")
        err = urllib.error.HTTPError(
            url="https://www.sec.gov",
            code=429,
            msg="Too Many Requests",
            hdrs=MagicMock(),
            fp=io.BytesIO(),
        )
        with patch("urllib.request.urlopen", side_effect=err):
            results = fetcher.fetch_all()

        assert len(results) == 3
        for _, path in results.items():
            assert path.exists() and path.stat().st_size > 0

    def test_fetcher_live_mode_raises_on_network_failure(self, tmp_path: Path) -> None:
        """Assert fetcher re-raises network errors when explicit mode='live' is requested."""
        fetcher = PublicDataFetcher(output_dir=tmp_path / "out", mode="live")

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Network unreachable"),
        ), pytest.raises(urllib.error.URLError):
            fetcher.fetch_sec_security_master()

    def test_fetcher_corrupted_sec_json_response_handling(self, tmp_path: Path) -> None:
        """Assert fetcher falls back to fixture if SEC returns malformed JSON or empty dict."""
        fetcher = PublicDataFetcher(output_dir=tmp_path / "out", mode="auto")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"not_a_valid_ticker_dict": []}'
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            out_path = fetcher.fetch_sec_security_master()

        assert out_path.exists()
        # Verify fallback fixture content was copied
        with open(out_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            assert header == ["ticker", "cik", "company_name", "exchange"]

    def test_fetcher_corrupted_fred_csv_response_handling(self, tmp_path: Path) -> None:
        """Assert fetcher falls back to fixture if FRED returns non-tabular HTML error page."""
        fetcher = PublicDataFetcher(output_dir=tmp_path / "out", mode="auto")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = (
            b"<html><head><title>Error</title></head><body>Server Busy</body></html>"
        )
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            out_path = fetcher.fetch_fred_treasury_rates()

        assert out_path.exists()
        with open(out_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            assert header == ["observation_date", "series_id", "yield_pct"]

    def test_missing_golden_fixture_raises_filenotfound(self, tmp_path: Path) -> None:
        """Assert _copy_fixture raises FileNotFoundError when source fixture does not exist."""
        empty_dir = tmp_path / "empty_fixtures"
        empty_dir.mkdir()
        fetcher = PublicDataFetcher(
            output_dir=tmp_path / "out", fixture_dir=empty_dir, mode="fixture"
        )

        with pytest.raises(FileNotFoundError, match="Offline golden fixture missing"):
            fetcher.fetch_sec_security_master()


# ==============================================================================
# SECTION 2: DUCKDB SHADOW SANDBOX 4-ZONE DDL STRESS & INTEGRITY ORACLES
# ==============================================================================


@pytest.fixture
def sandbox_db() -> duckdb.DuckDBPyConnection:
    """Fixture providing fresh in-memory DuckDB instance with compiled 4-Zone DDL."""
    con = duckdb.connect(database=":memory:")
    statements = [stmt.strip() for stmt in CANONICAL_FOUR_ZONE_DDL.split(";") if stmt.strip()]
    for stmt in statements:
        lines = [line for line in stmt.splitlines() if not line.strip().startswith("--")]
        clean_stmt = "\n".join(lines).strip()
        if clean_stmt:
            con.execute(clean_stmt)
    return con


class TestDuckDBShadowSandboxStress:
    """Stress-test DuckDB 4-Zone DDL: PK uniqueness, zero FK orphans, and boundary values."""

    def test_ddl_idempotency(self, sandbox_db: duckdb.DuckDBPyConnection) -> None:
        """Assert 4-Zone DDL is strictly idempotent when executed repeatedly."""
        statements = [stmt.strip() for stmt in CANONICAL_FOUR_ZONE_DDL.split(";") if stmt.strip()]
        for stmt in statements:
            lines = [line for line in stmt.splitlines() if not line.strip().startswith("--")]
            clean_stmt = "\n".join(lines).strip()
            if clean_stmt:
                sandbox_db.execute(clean_stmt)

    def test_hub_primary_key_uniqueness_enforcement(
        self, sandbox_db: duckdb.DuckDBPyConnection
    ) -> None:
        """Assert DuckDB enforces PK uniqueness on raw_vault hubs (rejects duplicate hash keys)."""
        sandbox_db.execute(
            "INSERT INTO raw_vault.hub_security VALUES "
            "('hk_sec_dup', 'AAPL', '0000320193', NOW(), 'EDGAR')"
        )
        with pytest.raises(duckdb.ConstraintException):
            sandbox_db.execute(
                "INSERT INTO raw_vault.hub_security VALUES "
                "('hk_sec_dup', 'MSFT', '0000789019', NOW(), 'EDGAR')"
            )

    def test_zero_referential_orphans_oracle(
        self, sandbox_db: duckdb.DuckDBPyConnection
    ) -> None:
        """Oracle: Verify that link records reference valid parent hubs with zero orphans."""
        investors = [
            ("hk_inv_01", "0001067983", "EDGAR"),
            ("hk_inv_02", "0001350694", "EDGAR"),
        ]
        securities = [
            ("hk_sec_01", "AAPL", "0000320193", "EDGAR"),
            ("hk_sec_02", "AMZN", "0001018724", "EDGAR"),
        ]
        for hk, cik, src in investors:
            sandbox_db.execute(
                f"INSERT INTO raw_vault.hub_investor VALUES ('{hk}', '{cik}', NOW(), '{src}')"
            )
        for hk, ticker, cik, src in securities:
            sandbox_db.execute(
                f"INSERT INTO raw_vault.hub_security VALUES "
                f"('{hk}', '{ticker}', '{cik}', NOW(), '{src}')"
            )

        links = [
            ("hk_lnk_01", "hk_inv_01", "hk_sec_01", "EDGAR"),
            ("hk_lnk_02", "hk_inv_01", "hk_sec_02", "EDGAR"),
            ("hk_lnk_03", "hk_inv_02", "hk_sec_01", "EDGAR"),
        ]
        for hk_lnk, hk_inv, hk_sec, src in links:
            sandbox_db.execute(
                f"INSERT INTO raw_vault.lnk_investor_holding VALUES "
                f"('{hk_lnk}', '{hk_inv}', '{hk_sec}', NOW(), '{src}')"
            )

        sandbox_db.execute(
            "INSERT INTO raw_vault.sat_holding_position VALUES "
            "('hk_lnk_01', 'HOLD_01', 5000000, 1150000000.00, 'SOLE', NOW(), 'EDGAR', 'diff_1')"
        )
        sandbox_db.execute(
            "INSERT INTO business_vault.bridge_portfolio_holdings VALUES "
            "('hk_lnk_01', 'hk_inv_01', 'hk_sec_01')"
        )

        # 4. Assert zero orphans on link -> investor hub
        orphan_inv_count = sandbox_db.execute(
            "SELECT COUNT(*) FROM raw_vault.lnk_investor_holding l "
            "LEFT JOIN raw_vault.hub_investor i ON l.hk_investor = i.hk_investor "
            "WHERE i.hk_investor IS NULL"
        ).fetchone()[0]
        assert orphan_inv_count == 0, f"Detected {orphan_inv_count} orphan investor link records"

        # 5. Assert zero orphans on link -> security hub
        orphan_sec_count = sandbox_db.execute(
            "SELECT COUNT(*) FROM raw_vault.lnk_investor_holding l "
            "LEFT JOIN raw_vault.hub_security s ON l.hk_security = s.hk_security "
            "WHERE s.hk_security IS NULL"
        ).fetchone()[0]
        assert orphan_sec_count == 0, f"Detected {orphan_sec_count} orphan security link records"

        # 6. Assert zero orphans on satellite -> link
        orphan_sat_count = sandbox_db.execute(
            "SELECT COUNT(*) FROM raw_vault.sat_holding_position sat "
            "LEFT JOIN raw_vault.lnk_investor_holding l ON "
            "sat.hk_investor_holding = l.hk_investor_holding "
            "WHERE l.hk_investor_holding IS NULL"
        ).fetchone()[0]
        assert orphan_sat_count == 0, f"Detected {orphan_sat_count} orphan satellite records"

        # 7. Assert zero orphans on bridge -> link
        orphan_bridge_count = sandbox_db.execute(
            "SELECT COUNT(*) FROM business_vault.bridge_portfolio_holdings b "
            "LEFT JOIN raw_vault.lnk_investor_holding l ON "
            "b.hk_investor_holding = l.hk_investor_holding "
            "WHERE l.hk_investor_holding IS NULL"
        ).fetchone()[0]
        assert orphan_bridge_count == 0, f"Detected {orphan_bridge_count} orphan bridge records"

    def test_zero_key_records_conformance(
        self, sandbox_db: duckdb.DuckDBPyConnection
    ) -> None:
        """Verify handling of Data Vault 2.0 zero-key records (ghost records for parents)."""
        zero_key_hk = "00000000000000000000000000000000"
        sandbox_db.execute(
            f"INSERT INTO raw_vault.hub_security VALUES "
            f"('{zero_key_hk}', 'UNKNOWN', '0000000000', '1970-01-01 00:00:00', 'SYSTEM')"
        )
        sandbox_db.execute(
            f"INSERT INTO raw_vault.hub_investor VALUES "
            f"('{zero_key_hk}', '0000000000', '1970-01-01 00:00:00', 'SYSTEM')"
        )

        sandbox_db.execute(
            f"INSERT INTO raw_vault.lnk_investor_holding VALUES "
            f"('hk_lnk_ghost', '{zero_key_hk}', '{zero_key_hk}', NOW(), 'SYSTEM')"
        )

        count = sandbox_db.execute(
            "SELECT COUNT(*) FROM raw_vault.lnk_investor_holding l "
            "JOIN raw_vault.hub_security s ON l.hk_security = s.hk_security "
            "WHERE s.bk_ticker = 'UNKNOWN'"
        ).fetchone()[0]
        assert count == 1, "Zero-key ghost record failed to join correctly"

    def test_extreme_decimal_boundaries(
        self, sandbox_db: duckdb.DuckDBPyConnection
    ) -> None:
        """Assert decimal boundary values in market_value_usd and yield_pct."""
        max_usd = Decimal("9999999999999999.99")
        sandbox_db.execute(
            f"INSERT INTO raw_vault.sat_holding_position VALUES "
            f"('hk_test_dec', 'H1', 1000, {max_usd}, 'SOLE', NOW(), 'EDGAR', 'diff_dec')"
        )
        res = sandbox_db.execute(
            "SELECT market_value_usd FROM raw_vault.sat_holding_position "
            "WHERE hk_investor_holding = 'hk_test_dec'"
        ).fetchone()[0]
        assert Decimal(str(res)) == max_usd

        neg_rate = Decimal("-0.7500")
        sandbox_db.execute(
            f"INSERT INTO raw_vault.sat_rate_observation VALUES "
            f"('hk_rate_neg', '2020-04-01', {neg_rate}, NOW(), 'FRED', 'diff_neg')"
        )
        rate_res = sandbox_db.execute(
            "SELECT yield_pct FROM raw_vault.sat_rate_observation "
            "WHERE hk_rate_index = 'hk_rate_neg'"
        ).fetchone()[0]
        assert Decimal(str(rate_res)) == neg_rate

    def test_sql_injection_and_special_character_safety(
        self, sandbox_db: duckdb.DuckDBPyConnection
    ) -> None:
        """Assert table structures safely handle SQL injection strings and unicode characters."""
        adversarial_strings = [
            "'; DROP TABLE raw_vault.hub_security; --",
            "Berkshire Hathaway Inc. <Class B> & Co.",
            "Crédit Agricole S.A. 🏦 (Paris)",
            '"><script>alert(1)</script>',
            "NULL",
            "\\x00\\x01\\x02",
        ]
        for idx, adv_str in enumerate(adversarial_strings):
            hk = f"hk_adv_{idx}"
            sandbox_db.execute(
                "INSERT INTO raw_vault.hub_security VALUES (?, ?, ?, NOW(), ?)",
                [hk, f"TICK{idx}", f"000000000{idx}", adv_str],
            )
            val = sandbox_db.execute(
                "SELECT record_source FROM raw_vault.hub_security WHERE hk_security = ?", [hk]
            ).fetchone()[0]
            assert val == adv_str

        tables = sandbox_db.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'raw_vault' AND table_name = 'hub_security'"
        ).fetchall()
        assert len(tables) == 1, "hub_security was compromised by SQL injection token"
