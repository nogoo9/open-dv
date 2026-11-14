"""tests/fixtures/public_am/fetch_public_datasets.py.

Public keyless dataset fetcher for SEC EDGAR and FRED with deterministic offline fixture fallback.
Provides both PublicDataFetcher class and command-line execution interface.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal

logger = logging.getLogger("opendv.fixtures.public_data_fetcher")

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FRED_DGS10_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
USER_AGENT = "OpenDVResearch/1.0 (research@open-dv.dev)"
REQUEST_TIMEOUT_SECONDS = 5.0
MAX_SEC_EQUITIES_COUNT = 50
MAX_FRED_OBSERVATION_COUNT = 30

FetchMode = Literal["auto", "live", "fixture"]

SEC_SECURITY_MASTER_FILENAME = "sec_security_master.csv"
SEC_13F_HOLDINGS_FILENAME = "sec_13f_holdings.csv"
FRED_TREASURY_RATES_FILENAME = "fred_treasury_rates.csv"


class PublicDataFetcher:
    """Fetcher for Asset Management public datasets with deterministic fallback."""

    def __init__(
        self,
        output_dir: Path | str,
        fixture_dir: Path | str | None = None,
        mode: FetchMode = "auto",
    ) -> None:
        self.output_dir = Path(output_dir)
        self.fixture_dir = Path(fixture_dir or Path(__file__).resolve().parent)
        self.mode: FetchMode = mode

    def fetch_all(self) -> dict[str, Path]:
        """Fetch all three public datasets into the output directory."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        results: dict[str, Path] = {}
        for name, fetch_fn in [
            (SEC_SECURITY_MASTER_FILENAME, self.fetch_sec_security_master),
            (SEC_13F_HOLDINGS_FILENAME, self.fetch_sec_13f_holdings),
            (FRED_TREASURY_RATES_FILENAME, self.fetch_fred_treasury_rates),
        ]:
            results[name] = fetch_fn()
        return results

    def fetch_sec_security_master(self) -> Path:
        """Fetch SEC security master dataset from SEC EDGAR or fallback to fixture."""
        out_path = self.output_dir / SEC_SECURITY_MASTER_FILENAME
        if self.mode != "fixture":
            try:
                req = urllib.request.Request(SEC_TICKERS_URL, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                    if resp.status == 200:
                        raw_bytes = resp.read()
                        data = json.loads(raw_bytes.decode("utf-8"))
                        self.output_dir.mkdir(parents=True, exist_ok=True)
                        with open(out_path, "w", newline="", encoding="utf-8") as f:
                            writer = csv.writer(f)
                            writer.writerow(["ticker", "cik", "company_name", "exchange"])
                            limit = min(MAX_SEC_EQUITIES_COUNT, len(data))
                            for i in range(limit):
                                item = data[str(i)]
                                ticker = item["ticker"]
                                cik = str(item["cik_str"]).zfill(10)
                                company_name = item["title"]
                                writer.writerow([ticker, cik, company_name, "US_EQUITY"])
                        logger.info("Successfully fetched live SEC security master: %d rows", limit)
                        return out_path
            except Exception as exc:
                logger.warning("Live SEC fetch failed (%s); falling back to fixture", exc)
                if self.mode == "live":
                    raise

        return self._copy_fixture(SEC_SECURITY_MASTER_FILENAME, out_path)

    def fetch_fred_treasury_rates(self) -> Path:
        """Fetch FRED 10-Yr Treasury yield dataset or fallback to fixture."""
        out_path = self.output_dir / FRED_TREASURY_RATES_FILENAME
        if self.mode != "fixture":
            try:
                req = urllib.request.Request(FRED_DGS10_URL, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                    if resp.status == 200:
                        lines = resp.read().decode("utf-8").splitlines()
                        self.output_dir.mkdir(parents=True, exist_ok=True)
                        with open(out_path, "w", newline="", encoding="utf-8") as f:
                            writer = csv.writer(f)
                            writer.writerow(["observation_date", "series_id", "yield_pct"])
                            reader = csv.reader(lines)
                            _header = next(reader, None)
                            count = 0
                            for row in reversed(list(reader)):
                                if len(row) >= 2 and row[1].strip() not in (".", "", "ND"):
                                    writer.writerow([row[0], "DGS10", row[1].strip()])
                                    count += 1
                                    if count >= MAX_FRED_OBSERVATION_COUNT:
                                        break
                        logger.info("Successfully fetched live FRED yields: %d rows", count)
                        return out_path
            except Exception as exc:
                logger.warning("Live FRED fetch failed (%s); falling back to fixture", exc)
                if self.mode == "live":
                    raise

        return self._copy_fixture(FRED_TREASURY_RATES_FILENAME, out_path)

    def fetch_sec_13f_holdings(self) -> Path:
        """Stage SEC 13F-HR institutional holdings dataset from offline fixture."""
        out_path = self.output_dir / SEC_13F_HOLDINGS_FILENAME
        return self._copy_fixture(SEC_13F_HOLDINGS_FILENAME, out_path)

    def _copy_fixture(self, filename: str, target_path: Path) -> Path:
        src = self.fixture_dir / filename
        if not src.exists():
            raise FileNotFoundError(f"Offline golden fixture missing: {src}")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        target_path.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        logger.info("Staged offline fixture: %s -> %s", src, target_path)
        return target_path


def fetch_public_am_datasets(
    output_dir: Path | str = "data/public_am",
    mode: FetchMode = "auto",
    fixture_dir: Path | str | None = None,
) -> dict[str, Path]:
    """Retrieve or stage SEC EDGAR and FRED Asset Management datasets.

    Adheres to SCOPE.md Interface Contract 1.
    """
    fetcher = PublicDataFetcher(output_dir=output_dir, fixture_dir=fixture_dir, mode=mode)
    return fetcher.fetch_all()


__all__ = [
    "FetchMode",
    "PublicDataFetcher",
    "fetch_public_am_datasets",
]


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for fetching public datasets."""
    parser = argparse.ArgumentParser(
        description=(
            "Public keyless dataset fetcher for SEC EDGAR and FRED (Open-DV Asset Management)"
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "live", "fixture"],
        default="auto",
        help="Fetch mode: 'auto' (live with fallback), 'live', or 'fixture'",
    )
    parser.add_argument(
        "--output-dir",
        default="data/public_am",
        help="Target output directory for staged datasets",
    )
    parser.add_argument(
        "--fixture-dir",
        default=None,
        help="Source directory containing offline golden fixtures",
    )
    args = parser.parse_args(argv)

    fetcher = PublicDataFetcher(
        output_dir=args.output_dir,
        fixture_dir=args.fixture_dir,
        mode=args.mode,
    )
    try:
        results = fetcher.fetch_all()
        for name, path in results.items():
            print(f"[OK] {name} -> {path} ({path.stat().st_size} bytes)")
        return 0
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"[ERROR] Fetch failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
