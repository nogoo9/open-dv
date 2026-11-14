"""tests/e2e/test_browser_pipeline_builder.py.

Automated pytest runner for the agentic browser E2E walkthrough (Features 16-18).
Validates public dataset staging, genuine Playwright walkthrough execution, screenshot capture,
and DuckDB shadow sandbox 4-zone DDL compilation.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import duckdb
from tests.fixtures.public_am.fetch_public_datasets import PublicDataFetcher

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKBENCH_DIR = REPO_ROOT / "apps" / "opendv-web-workbench"
ARTIFACTS_DIR = REPO_ROOT / "artifacts"
SCREENSHOTS_DIR = ARTIFACTS_DIR / "screenshots"

CANONICAL_FOUR_ZONE_DDL: str = (
    (ARTIFACTS_DIR / "final_four_zone_am.sql").read_text(encoding="utf-8")
    if (ARTIFACTS_DIR / "final_four_zone_am.sql").exists()
    else ""
)

DEFAULT_CHROMIUM_PATH = (
    r"C:\Users\etern\AppData\Local\ms-playwright\chromium-1243\chrome-win64\chrome.exe"
)
CHROMIUM_BINARY = Path(
    os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", DEFAULT_CHROMIUM_PATH)
)

EXPECTED_SCREENSHOTS = [
    "01_ontology_studio_initial.png",
    "02_ontology_bootstrapped.png",
    "03_steering_proposal_applied.png",
    "04_dv_blueprint_ddl_preview.png",
    "05_pipeline_builder_prefilled.png",
    "06_pipeline_spec_validated.png",
    "07_pipeline_execution_completed.png",
    "08_final_ddl_exported.png",
]

MIN_SCREENSHOT_BYTES = 10_000


def test_chromium_binary_present() -> None:
    """Verify local pre-installed Chromium binary is available."""
    assert CHROMIUM_BINARY.exists(), f"Chromium binary not found at {CHROMIUM_BINARY}"


def test_public_dataset_staging(tmp_path: Path) -> None:
    """Verify PublicDataFetcher stages all three public datasets."""
    fetcher = PublicDataFetcher(
        output_dir=tmp_path / "public_am",
        mode="fixture",
    )
    results = fetcher.fetch_all()
    assert len(results) == 3
    for name, path in results.items():
        assert path.exists(), f"Staged file {name} not found"
        assert path.stat().st_size > 0, f"Staged file {name} is empty"


def test_browser_e2e_walkthrough() -> None:
    """Execute genuine Playwright browser E2E walkthrough test and assert authentic artifacts."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure public datasets are staged
    fetcher = PublicDataFetcher(output_dir=REPO_ROOT / "data" / "public_am", mode="fixture")
    fetcher.fetch_all()

    # Execute genuine Playwright test via bun runner
    bun_exe = shutil.which("bun")
    assert bun_exe is not None, "bun executable is required for browser E2E walkthrough"
    assert WORKBENCH_DIR.is_dir(), f"Workbench directory missing at {WORKBENCH_DIR}"

    env = {
        **os.environ,
        "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH": str(CHROMIUM_BINARY),
    }
    cmd = [
        bun_exe,
        "x",
        "playwright",
        "test",
        "tests/e2e/browser_e2e_walkthrough.spec.ts",
    ]
    subprocess.run(
        cmd,
        cwd=str(WORKBENCH_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )

    # Assert genuine screenshots produced by Playwright (>10KB each)
    for sc in EXPECTED_SCREENSHOTS:
        sc_path = SCREENSHOTS_DIR / sc
        assert sc_path.exists(), f"Expected screenshot {sc} missing in {SCREENSHOTS_DIR}"
        assert sc_path.stat().st_size >= MIN_SCREENSHOT_BYTES, (
            f"Screenshot {sc} is too small ({sc_path.stat().st_size} bytes)"
        )

    # Assert exported DDL artifact was produced by the browser walkthrough
    ddl_path = ARTIFACTS_DIR / "final_four_zone_am.sql"
    assert ddl_path.exists(), (
        f"Exported DDL {ddl_path} missing — must be produced by browser walkthrough"
    )

    ddl_content = ddl_path.read_text(encoding="utf-8")
    assert "hub_security" in ddl_content
    assert "lnk_investor_holding" in ddl_content
    assert "sat_rate_observation" in ddl_content
    assert "CREATE SCHEMA IF NOT EXISTS raw_vault" in ddl_content


def test_duckdb_sandbox_ddl_compilation() -> None:
    """Compile exported 4-zone DDL in DuckDB shadow sandbox and verify table structures."""
    ddl_path = ARTIFACTS_DIR / "final_four_zone_am.sql"
    assert ddl_path.exists(), f"Exported DDL {ddl_path} missing"

    ddl_content = ddl_path.read_text(encoding="utf-8")
    con = duckdb.connect(database=":memory:")

    # Split and execute individual SQL statements
    statements = [stmt.strip() for stmt in ddl_content.split(";") if stmt.strip()]
    for stmt in statements:
        # Skip pure comments
        lines = [line for line in stmt.splitlines() if not line.strip().startswith("--")]
        clean_stmt = "\n".join(lines).strip()
        if clean_stmt:
            con.execute(clean_stmt)

    # Verify tables created across all 4 zones
    res = con.execute("SELECT table_schema, table_name FROM information_schema.tables").fetchall()
    table_map = {(row[0], row[1]) for row in res}

    expected_tables = {
        ("staging", "stg_sec_security_master"),
        ("staging", "stg_sec_13f_holdings"),
        ("staging", "stg_fred_treasury_rates"),
        ("raw_vault", "hub_security"),
        ("raw_vault", "hub_investor"),
        ("raw_vault", "hub_rate_index"),
        ("raw_vault", "hub_document"),
        ("raw_vault", "lnk_investor_holding"),
        ("raw_vault", "sat_security_details"),
        ("raw_vault", "sat_holding_position"),
        ("raw_vault", "sat_rate_observation"),
        ("raw_vault", "sat_document_metadata"),
        ("business_vault", "pit_security"),
        ("business_vault", "bridge_portfolio_holdings"),
        ("marts", "dim_security"),
        ("marts", "fct_institutional_holding"),
    }
    for schema_name, table_name in expected_tables:
        assert (schema_name, table_name) in table_map, (
            f"Table {schema_name}.{table_name} missing from compiled DuckDB database"
        )

    # Test sample insertion and referential check in sandbox
    con.execute(
        "INSERT INTO raw_vault.hub_security VALUES "
        "('hk_sec_01', 'AAPL', '0000320193', NOW(), 'SEC_EDGAR')"
    )
    con.execute(
        "INSERT INTO raw_vault.hub_investor VALUES "
        "('hk_inv_01', '0001067983', NOW(), 'SEC_EDGAR')"
    )
    con.execute(
        "INSERT INTO raw_vault.lnk_investor_holding VALUES "
        "('hk_lnk_01', 'hk_inv_01', 'hk_sec_01', NOW(), 'SEC_EDGAR')"
    )
    row_count = con.execute("SELECT COUNT(*) FROM raw_vault.lnk_investor_holding").fetchone()[0]
    assert row_count == 1

    con.close()
