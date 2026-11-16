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
from datetime import UTC

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKBENCH_DIR = REPO_ROOT / "apps" / "opendv-web-workbench"
ARTIFACTS_DIR = REPO_ROOT / "artifacts"
SCREENSHOTS_DIR = ARTIFACTS_DIR / "screenshots"

CANONICAL_FOUR_ZONE_DDL: str = (
    (ARTIFACTS_DIR / "final_four_zone_am.sql").read_text(encoding="utf-8")
    if (ARTIFACTS_DIR / "final_four_zone_am.sql").exists()
    else ""
)

def _discover_chromium() -> Path:
    env_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if env_path and Path(env_path).exists():
        return Path(env_path)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        pw_dir = Path(local_app_data) / "ms-playwright"
        if pw_dir.is_dir():
            candidates = sorted(pw_dir.glob("chromium-*/chrome-win64/chrome.exe"))
            if candidates:
                return candidates[-1]
            candidates_alt = sorted(pw_dir.glob("chromium-*/chrome-win*/chrome.exe"))
            if candidates_alt:
                return candidates_alt[-1]
    linux_pw = Path.home() / ".cache" / "ms-playwright"
    if linux_pw.is_dir():
        candidates_linux = sorted(linux_pw.glob("chromium-*/chrome-linux*/chrome"))
        if candidates_linux:
            return candidates_linux[-1]
    mac_pw = Path.home() / "Library" / "Caches" / "ms-playwright"
    if mac_pw.is_dir():
        candidates_mac = sorted(
            mac_pw.glob("chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium")
        )
        if candidates_mac:
            return candidates_mac[-1]
    for bin_name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "chrome",
    ):
        which = shutil.which(bin_name)
        if which:
            return Path(which)
    return Path("chrome")


CHROMIUM_BINARY = _discover_chromium()

EXPECTED_SCREENSHOTS = [
    "01_ontology_studio_initial.png",
    "02_ontology_bootstrapped.png",
    "03_steering_proposal_applied.png",
    "04_dv_blueprint_ddl_preview.png",
    "05_pipeline_builder_prefilled.png",
    "06_run1_step2_sqlite_configured.png",
    "07_run1_step3_governance.png",
    "08_run1_execution_diff_complete.png",
    "09_run2_step1_evolution_mode.png",
    "10_run2_step2_hybrid_sources_configured.png",
    "11_run2_step3_governance.png",
    "12_run2_hybrid_evolution_diff_complete.png",
    "13_final_multi_dialect_ddl_exported.png",
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
    assert len(results) == 4
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

    # Playwright's test.beforeAll seeds the Studio session via Platform REST API.
    # No disk mutation performed here.

    # Execute genuine Playwright test via bun runner
    bun_exe = shutil.which("bun")
    assert bun_exe is not None, "bun executable is required for browser E2E walkthrough"
    assert WORKBENCH_DIR.is_dir(), f"Workbench directory missing at {WORKBENCH_DIR}"

    env = {
        **os.environ,
        "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH": str(CHROMIUM_BINARY),
        "OPENDV_ALLOWED_HOSTS": os.environ.get(
            "OPENDV_ALLOWED_HOSTS", "127.0.0.1,localhost,am_prod,uc://am_prod"
        ),
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
        timeout=720,
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
    assert "hub_issuer" in ddl_content
    assert ("lnk_security_issuer" in ddl_content or "lnk_securities_issuer" in ddl_content)
    assert "CREATE SCHEMA IF NOT EXISTS raw_vault" in ddl_content

    # Assert exported Databricks Delta DDL artifact was produced by browser walkthrough
    dbx_path = ARTIFACTS_DIR / "final_databricks_delta_am.sql"
    assert dbx_path.exists(), f"Exported Databricks DDL {dbx_path} missing — must be produced by browser walkthrough"
    dbx_content = dbx_path.read_text(encoding="utf-8")
    assert "USING DELTA" in dbx_content
    assert "MERGE INTO" in dbx_content


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
        ("staging", "stg_am_sec_treasury_ibor_securities"),
        ("staging", "stg_am_sec_treasury_ibor_treasury_rates"),
        ("staging", "stg_hub_rate"),
        ("staging", "stg_hub_issuer"),
        ("staging", "stg_hub_security"),
        ("staging", "stg_hub_securities"),
        ("staging", "stg_lnk_securities_issuer"),
        ("staging", "stg_sat_securities"),
        ("staging", "stg_sat_treasury_rates"),
        ("raw_vault", "hub_security"),
        ("raw_vault", "hub_securities"),
        ("raw_vault", "hub_issuer"),
        ("raw_vault", "hub_rate"),
        ("raw_vault", "lnk_securities_issuer"),
        ("raw_vault", "sat_securities"),
        ("raw_vault", "sat_treasury_rates"),
        ("business_vault", "pit_position"),
        ("business_vault", "snap_logarithmic_calendar"),
        ("information_mart", "pit_position"),
        ("information_mart", "snap_logarithmic_calendar"),
        ("information_mart", "vw_pit_position_current"),
    }
    for schema_name, table_name in expected_tables:
        assert (schema_name, table_name) in table_map, (
            f"Table {schema_name}.{table_name} missing from compiled DuckDB database"
        )

    # Test sample insertion and referential check in sandbox
    con.execute(
        "INSERT INTO raw_vault.hub_securities VALUES "
        "('hk_sec_test', 'AM_SEC_TREASURY_IBOR_SECURITIES', 'TEST_AAPL', NOW(), 'TEST_FEED')"
    )
    con.execute(
        "INSERT INTO raw_vault.hub_issuer VALUES "
        "('hk_iss_test', 'AM_SEC_TREASURY_IBOR_SECURITIES', 'TEST_0000320193', NOW(), 'TEST_FEED')"
    )
    con.execute(
        "INSERT INTO raw_vault.lnk_securities_issuer VALUES "
        "('hk_lnk_test', 'hk_sec_test', 'hk_iss_test', NOW(), 'TEST_FEED')"
    )
    row_count = con.execute("SELECT COUNT(*) FROM raw_vault.lnk_securities_issuer WHERE hk_lnk_securities_issuer = 'hk_lnk_test'").fetchone()[0]
    assert row_count == 1

    con.close()
