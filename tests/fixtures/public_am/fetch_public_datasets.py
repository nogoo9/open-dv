"""Public keyless dataset fetcher for SEC EDGAR, Treasury, and FRED (WS2A.4).

Manages declarative dataset downloads and offline fixture staging driven by
dataset_manifest.yaml, producing normalized SQLite databases with PK/FK DDL.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sqlite3
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml

logger = logging.getLogger("opendv.fixtures.public_data_fetcher")

FetchMode = Literal["auto", "live", "fixture"]

MANIFEST_FILENAME = "dataset_manifest.yaml"
AM_SQLITE_FILENAME = "am_sec_treasury_ibor.sqlite"
SEC_SECURITY_MASTER_FILENAME = "sec_security_master.csv"
SEC_13F_HOLDINGS_FILENAME = "sec_13f_holdings.csv"
FRED_TREASURY_RATES_FILENAME = "fred_treasury_rates.csv"
NORTHWIND_FILENAME = "northwind.sqlite"
CHINOOK_FILENAME = "chinook.sqlite"


def load_dataset_manifest(fixture_dir: Path | str | None = None) -> dict[str, Any]:
    """Load and parse the declarative dataset manifest."""
    base_dir = Path(fixture_dir or Path(__file__).resolve().parent)
    manifest_path = base_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        default_manifest = Path(__file__).resolve().parent / MANIFEST_FILENAME
        if default_manifest.is_file():
            manifest_path = default_manifest
        else:
            raise FileNotFoundError(f"Missing dataset manifest: {manifest_path}")
    raw_content = manifest_path.read_text(encoding="utf-8")
    parsed: Any = yaml.safe_load(raw_content)
    if not isinstance(parsed, dict) or "suites" not in parsed:
        raise ValueError(f"Invalid dataset manifest schema at {manifest_path}")
    return parsed


_DEFAULT_FETCH_CONFIG = load_dataset_manifest().get("fetch_config", {})
MAX_SEC_EQUITIES_COUNT: int = int(_DEFAULT_FETCH_CONFIG.get("max_sec_equities_count", 0))
MAX_FRED_OBSERVATION_COUNT: int = int(_DEFAULT_FETCH_CONFIG.get("max_fred_observations_count", 0))


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
        self._manifest: dict[str, Any] = load_dataset_manifest(self.fixture_dir)
        self._config: dict[str, Any] = self._manifest.get("fetch_config", {})

    @property
    def user_agent(self) -> str:
        return str(self._config.get("user_agent", "OpenDVResearch/1.0 (research@open-dv.dev)"))

    @property
    def request_timeout(self) -> float:
        return float(self._config.get("request_timeout_seconds", 5.0))

    def fetch_all(self) -> dict[str, Path]:
        """Fetch all public datasets including individual CSVs and the unified SQLite database."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        sec_path = self.fetch_sec_security_master()
        holdings_path = self.fetch_sec_13f_holdings()
        treasury_path = self.fetch_fred_treasury_rates()
        sqlite_path = self.fetch_am_sqlite(
            sec_master_path=sec_path,
            holdings_path=holdings_path,
            treasury_path=treasury_path,
        )
        results: dict[str, Path] = {
            SEC_SECURITY_MASTER_FILENAME: sec_path,
            SEC_13F_HOLDINGS_FILENAME: holdings_path,
            FRED_TREASURY_RATES_FILENAME: treasury_path,
            AM_SQLITE_FILENAME: sqlite_path,
        }
        return results

    def fetch_sec_security_master(self) -> Path:
        """Fetch SEC security master dataset from SEC EDGAR or fallback to fixture."""
        out_path = self.output_dir / SEC_SECURITY_MASTER_FILENAME
        src_meta = self._manifest["suites"]["asset_management"]["sources"]["sec_company_tickers"]
        url = src_meta.get("url")
        max_equities = int(self._config.get("max_sec_equities_count", 50))

        if self.mode != "fixture" and url:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
                with urllib.request.urlopen(req, timeout=self.request_timeout) as resp:
                    if resp.status == 200:
                        raw_bytes = resp.read()
                        data = json.loads(raw_bytes.decode("utf-8"))
                        self.output_dir.mkdir(parents=True, exist_ok=True)
                        with open(out_path, "w", newline="", encoding="utf-8") as f:
                            writer = csv.writer(f)
                            writer.writerow(["ticker", "cik", "company_name", "exchange"])
                            limit = min(max_equities, len(data))
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
        src_meta = self._manifest["suites"]["asset_management"]["sources"]["fred_treasury_yields"]
        url = src_meta.get("url")
        max_obs = int(self._config.get("max_fred_observations_count", 30))

        if self.mode != "fixture" and url:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
                with urllib.request.urlopen(req, timeout=self.request_timeout) as resp:
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
                                    if count >= max_obs:
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

    def fetch_am_sqlite(
        self,
        sec_master_path: Path | None = None,
        holdings_path: Path | None = None,
        treasury_path: Path | None = None,
    ) -> Path:
        """Construct normalized Asset Management SQLite database with PK/FK constraints."""
        out_path = self.output_dir / AM_SQLITE_FILENAME
        self.output_dir.mkdir(parents=True, exist_ok=True)

        sec_master_path = sec_master_path or self.fetch_sec_security_master()
        holdings_path = holdings_path or self.fetch_sec_13f_holdings()
        treasury_path = treasury_path or self.fetch_fred_treasury_rates()

        if out_path.exists():
            out_path.unlink()

        conn = sqlite3.connect(out_path)
        cur = conn.cursor()

        # DDL Execution with full PK/FK constraints
        cur.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS issuers (
                cik TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                company_name TEXT NOT NULL,
                exchange TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS securities (
                ticker TEXT PRIMARY KEY,
                cik TEXT NOT NULL,
                cusip TEXT,
                security_type TEXT NOT NULL DEFAULT 'COMMON_STOCK',
                FOREIGN KEY (cik) REFERENCES issuers(cik)
            );

            CREATE TABLE IF NOT EXISTS institutional_filers (
                filer_cik TEXT PRIMARY KEY,
                filer_name TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS holdings_13f (
                holding_id TEXT PRIMARY KEY,
                filer_cik TEXT NOT NULL,
                cusip TEXT NOT NULL,
                ticker TEXT NOT NULL,
                issuer_name TEXT NOT NULL,
                shares INTEGER NOT NULL,
                market_value_usd REAL NOT NULL,
                investment_discretion TEXT NOT NULL,
                FOREIGN KEY (ticker) REFERENCES securities(ticker),
                FOREIGN KEY (filer_cik) REFERENCES institutional_filers(filer_cik)
            );

            CREATE TABLE IF NOT EXISTS treasury_rates (
                observation_date TEXT NOT NULL,
                series_id TEXT NOT NULL,
                yield_pct REAL NOT NULL,
                PRIMARY KEY (observation_date, series_id)
            );

            CREATE TABLE IF NOT EXISTS yield_curves (
                curve_id TEXT PRIMARY KEY,
                observation_date TEXT NOT NULL,
                rate_2y REAL,
                rate_10y REAL,
                rate_sofr REAL,
                spread_10y_2y REAL
            );
            """
        )

        # 1. Ingest issuers and securities
        issuers_seen: set[str] = set()
        tickers_seen: set[str] = set()
        with open(sec_master_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cik = str(row["cik"]).strip()
                ticker = str(row["ticker"]).strip()
                name = str(row["company_name"]).strip()
                exchange = str(row.get("exchange", "US_EQUITY")).strip()

                if cik not in issuers_seen:
                    cur.execute(
                        "INSERT INTO issuers (cik, ticker, company_name, exchange) VALUES (?, ?, ?, ?)",
                        (cik, ticker, name, exchange),
                    )
                    issuers_seen.add(cik)

                if ticker not in tickers_seen and cik in issuers_seen:
                    cur.execute(
                        "INSERT INTO securities (ticker, cik, cusip, security_type) VALUES (?, ?, ?, ?)",
                        (ticker, cik, None, "COMMON_STOCK"),
                    )
                    tickers_seen.add(ticker)

        # 2. Ingest institutional filers & 13F holdings
        filers_seen: set[str] = set()
        with open(holdings_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                holding_id = str(row["holding_id"]).strip()
                cik = str(row["cik"]).strip()
                cusip = str(row["cusip"]).strip()
                ticker = str(row["ticker"]).strip()
                issuer = str(row["issuer_name"]).strip()
                shares = int(float(row.get("shares", 0)))
                val = float(row.get("market_value_usd", 0.0))
                disc = str(row.get("investment_discretion", "SOLE")).strip()

                if cik not in filers_seen:
                    cur.execute(
                        "INSERT INTO institutional_filers (filer_cik, filer_name) VALUES (?, ?)",
                        (cik, f"Institutional Filer {cik}"),
                    )
                    filers_seen.add(cik)

                if ticker not in tickers_seen:
                    # Insert dummy issuer if missing to preserve FK integrity
                    if cik not in issuers_seen:
                        cur.execute(
                            "INSERT INTO issuers (cik, ticker, company_name, exchange) VALUES (?, ?, ?, ?)",
                            (cik, ticker, issuer, "US_EQUITY"),
                        )
                        issuers_seen.add(cik)
                    cur.execute(
                        "INSERT INTO securities (ticker, cik, cusip, security_type) VALUES (?, ?, ?, ?)",
                        (ticker, cik, cusip, "COMMON_STOCK"),
                    )
                    tickers_seen.add(ticker)
                else:
                    cur.execute(
                        "UPDATE securities SET cusip = ? WHERE ticker = ? AND cusip IS NULL",
                        (cusip, ticker),
                    )

                cur.execute(
                    """
                    INSERT INTO holdings_13f (
                        holding_id, filer_cik, cusip, ticker, issuer_name,
                        shares, market_value_usd, investment_discretion
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (holding_id, cik, cusip, ticker, issuer, shares, val, disc),
                )

        # 3. Ingest treasury rates
        rates_seen: set[tuple[str, str]] = set()
        with open(treasury_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                obs_date = str(row["observation_date"]).strip()
                series = str(row.get("series_id", "DGS10")).strip()
                try:
                    val = float(row["yield_pct"])
                except (ValueError, TypeError):
                    continue

                if (obs_date, series) not in rates_seen:
                    cur.execute(
                        "INSERT INTO treasury_rates (observation_date, series_id, yield_pct) VALUES (?, ?, ?)",
                        (obs_date, series, val),
                    )
                    rates_seen.add((obs_date, series))

                    # Synthetic term structure yield curve row
                    curve_id = f"CURVE_{obs_date}"
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO yield_curves (
                            curve_id, observation_date, rate_2y, rate_10y, rate_sofr, spread_10y_2y
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (curve_id, obs_date, round(val - 0.25, 2), val, 5.31, 0.25),
                    )

        conn.commit()
        conn.close()
        logger.info("Successfully constructed normalized SQLite database: %s", out_path)
        return out_path

    def fetch_northwind(self, target_dir: Path | str | None = None) -> Path:
        """Fetch or stage Northwind SQLite benchmark database."""
        out_dir = Path(target_dir or self.output_dir.parent / "public_sqlite")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / NORTHWIND_FILENAME

        bm_meta = self._manifest["suites"]["benchmarks"]["northwind"]
        url = bm_meta.get("url")

        if self.mode != "fixture" and url:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
                with urllib.request.urlopen(req, timeout=self.request_timeout) as resp:
                    if resp.status == 200:
                        out_path.write_bytes(resp.read())
                        logger.info("Successfully downloaded live Northwind SQLite: %s", out_path)
                        return out_path
            except Exception as exc:
                logger.warning("Live Northwind fetch failed (%s); staging offline fixture", exc)
                if self.mode == "live":
                    raise

        return self._create_northwind_offline_fixture(out_path)

    def fetch_chinook(self, target_dir: Path | str | None = None) -> Path:
        """Fetch or stage Chinook SQLite benchmark database."""
        out_dir = Path(target_dir or self.output_dir.parent / "public_sqlite")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / CHINOOK_FILENAME

        bm_meta = self._manifest["suites"]["benchmarks"]["chinook"]
        url = bm_meta.get("url")

        if self.mode != "fixture" and url:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
                with urllib.request.urlopen(req, timeout=self.request_timeout) as resp:
                    if resp.status == 200:
                        out_path.write_bytes(resp.read())
                        logger.info("Successfully downloaded live Chinook SQLite: %s", out_path)
                        return out_path
            except Exception as exc:
                logger.warning("Live Chinook fetch failed (%s); staging offline fixture", exc)
                if self.mode == "live":
                    raise

        return self._create_chinook_offline_fixture(out_path)

    def _create_northwind_offline_fixture(self, out_path: Path) -> Path:
        """Construct deterministic offline Northwind benchmark SQLite database."""
        if out_path.exists():
            out_path.unlink()
        conn = sqlite3.connect(out_path)
        cur = conn.cursor()
        cur.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE Categories (CategoryID INTEGER PRIMARY KEY, CategoryName TEXT NOT NULL);
            CREATE TABLE Suppliers (SupplierID INTEGER PRIMARY KEY, CompanyName TEXT NOT NULL);
            CREATE TABLE Customers (CustomerID TEXT PRIMARY KEY, CompanyName TEXT NOT NULL, ContactName TEXT, Country TEXT);
            CREATE TABLE Employees (EmployeeID INTEGER PRIMARY KEY, LastName TEXT NOT NULL, FirstName TEXT NOT NULL, ReportsTo INTEGER, FOREIGN KEY (ReportsTo) REFERENCES Employees(EmployeeID));
            CREATE TABLE Products (ProductID INTEGER PRIMARY KEY, ProductName TEXT NOT NULL, SupplierID INTEGER, CategoryID INTEGER, UnitPrice REAL, FOREIGN KEY (CategoryID) REFERENCES Categories(CategoryID), FOREIGN KEY (SupplierID) REFERENCES Suppliers(SupplierID));
            CREATE TABLE Orders (OrderID INTEGER PRIMARY KEY, CustomerID TEXT, EmployeeID INTEGER, OrderDate TEXT, FOREIGN KEY (CustomerID) REFERENCES Customers(CustomerID), FOREIGN KEY (EmployeeID) REFERENCES Employees(EmployeeID));
            CREATE TABLE [Order Details] (OrderID INTEGER NOT NULL, ProductID INTEGER NOT NULL, UnitPrice REAL NOT NULL, Quantity INTEGER NOT NULL, Discount REAL DEFAULT 0, PRIMARY KEY (OrderID, ProductID), FOREIGN KEY (OrderID) REFERENCES Orders(OrderID), FOREIGN KEY (ProductID) REFERENCES Products(ProductID));

            INSERT INTO Categories VALUES (1, 'Beverages'), (2, 'Condiments');
            INSERT INTO Suppliers VALUES (1, 'Exotic Liquids'), (2, 'New Orleans Cajun Delights');
            INSERT INTO Customers VALUES ('ALFKI', 'Alfreds Futterkiste', 'Maria Anders', 'Germany'), ('ANATR', 'Ana Trujillo Emparedados', 'Ana Trujillo', 'Mexico');
            INSERT INTO Employees VALUES (1, 'Davolio', 'Nancy', NULL), (2, 'Fuller', 'Andrew', 1);
            INSERT INTO Products VALUES (1, 'Chai', 1, 1, 18.0), (2, 'Chang', 1, 1, 19.0);
            INSERT INTO Orders VALUES (10248, 'ALFKI', 1, '1996-07-04'), (10249, 'ANATR', 2, '1996-07-05');
            INSERT INTO [Order Details] VALUES (10248, 1, 14.0, 12, 0.0), (10248, 2, 9.8, 10, 0.0);
            """
        )
        conn.commit()
        conn.close()
        return out_path

    def _create_chinook_offline_fixture(self, out_path: Path) -> Path:
        """Construct deterministic offline Chinook benchmark SQLite database."""
        if out_path.exists():
            out_path.unlink()
        conn = sqlite3.connect(out_path)
        cur = conn.cursor()
        cur.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE Artist (ArtistId INTEGER PRIMARY KEY, Name TEXT);
            CREATE TABLE Album (AlbumId INTEGER PRIMARY KEY, Title TEXT NOT NULL, ArtistId INTEGER NOT NULL, FOREIGN KEY (ArtistId) REFERENCES Artist(ArtistId));
            CREATE TABLE MediaType (MediaTypeId INTEGER PRIMARY KEY, Name TEXT);
            CREATE TABLE Genre (GenreId INTEGER PRIMARY KEY, Name TEXT);
            CREATE TABLE Track (TrackId INTEGER PRIMARY KEY, Name TEXT NOT NULL, AlbumId INTEGER, MediaTypeId INTEGER NOT NULL, GenreId INTEGER, UnitPrice REAL NOT NULL, FOREIGN KEY (AlbumId) REFERENCES Album(AlbumId), FOREIGN KEY (MediaTypeId) REFERENCES MediaType(MediaTypeId), FOREIGN KEY (GenreId) REFERENCES Genre(GenreId));
            CREATE TABLE Employee (EmployeeId INTEGER PRIMARY KEY, LastName TEXT NOT NULL, FirstName TEXT NOT NULL, ReportsTo INTEGER, FOREIGN KEY (ReportsTo) REFERENCES Employee(EmployeeId));
            CREATE TABLE Customer (CustomerId INTEGER PRIMARY KEY, FirstName TEXT NOT NULL, LastName TEXT NOT NULL, SupportRepId INTEGER, FOREIGN KEY (SupportRepId) REFERENCES Employee(EmployeeId));
            CREATE TABLE Invoice (InvoiceId INTEGER PRIMARY KEY, CustomerId INTEGER NOT NULL, InvoiceDate TEXT NOT NULL, Total REAL NOT NULL, FOREIGN KEY (CustomerId) REFERENCES Customer(CustomerId));
            CREATE TABLE InvoiceLine (InvoiceLineId INTEGER PRIMARY KEY, InvoiceId INTEGER NOT NULL, TrackId INTEGER NOT NULL, UnitPrice REAL NOT NULL, Quantity INTEGER NOT NULL, FOREIGN KEY (InvoiceId) REFERENCES Invoice(InvoiceId), FOREIGN KEY (TrackId) REFERENCES Track(TrackId));

            INSERT INTO Artist VALUES (1, 'AC/DC'), (2, 'Accept');
            INSERT INTO Album VALUES (1, 'For Those About To Rock We Salute You', 1);
            INSERT INTO MediaType VALUES (1, 'MPEG audio file');
            INSERT INTO Genre VALUES (1, 'Rock');
            INSERT INTO Track VALUES (1, 'For Those About To Rock', 1, 1, 1, 0.99);
            INSERT INTO Employee VALUES (1, 'Adams', 'Andrew', NULL);
            INSERT INTO Customer VALUES (1, 'Luís', 'Gonçalves', 1);
            INSERT INTO Invoice VALUES (1, 1, '2021-01-01', 1.98);
            INSERT INTO InvoiceLine VALUES (1, 1, 1, 0.99, 1);
            """
        )
        conn.commit()
        conn.close()
        return out_path

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
    """Retrieve or stage SEC EDGAR, Treasury, and FRED Asset Management datasets."""
    fetcher = PublicDataFetcher(output_dir=output_dir, fixture_dir=fixture_dir, mode=mode)
    return fetcher.fetch_all()


def fetch_benchmark_sqlite_datasets(
    output_dir: Path | str = "data/public_sqlite",
    mode: FetchMode = "auto",
    fixture_dir: Path | str | None = None,
) -> dict[str, Path]:
    """Retrieve or stage Northwind and Chinook SQLite benchmark databases."""
    fetcher = PublicDataFetcher(output_dir=output_dir, fixture_dir=fixture_dir, mode=mode)
    return {
        NORTHWIND_FILENAME: fetcher.fetch_northwind(output_dir),
        CHINOOK_FILENAME: fetcher.fetch_chinook(output_dir),
    }


__all__ = [
    "AM_SQLITE_FILENAME",
    "CHINOOK_FILENAME",
    "FRED_TREASURY_RATES_FILENAME",
    "MANIFEST_FILENAME",
    "MAX_FRED_OBSERVATION_COUNT",
    "MAX_SEC_EQUITIES_COUNT",
    "NORTHWIND_FILENAME",
    "SEC_13F_HOLDINGS_FILENAME",
    "SEC_SECURITY_MASTER_FILENAME",
    "FetchMode",
    "PublicDataFetcher",
    "fetch_benchmark_sqlite_datasets",
    "fetch_public_am_datasets",
    "load_dataset_manifest",
]


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for fetching public datasets."""
    parser = argparse.ArgumentParser(
        description="Public keyless dataset fetcher for Open-DV Asset Management & Benchmarks",
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
    parser.add_argument(
        "--benchmarks",
        action="store_true",
        help="Fetch benchmark SQLite databases (Northwind & Chinook)",
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

        if args.benchmarks:
            bm_results = fetch_benchmark_sqlite_datasets(
                output_dir=Path(args.output_dir).parent / "public_sqlite",
                mode=args.mode,
                fixture_dir=args.fixture_dir,
            )
            for name, path in bm_results.items():
                print(f"[OK Benchmark] {name} -> {path} ({path.stat().st_size} bytes)")

        return 0
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"[ERROR] Fetch failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
