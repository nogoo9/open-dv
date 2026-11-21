"""tests/e2e/test_browser_pipeline_builder.py.

Automated pytest runner for the agentic browser E2E walkthrough (Features 16-18).
Validates public dataset staging, genuine Playwright walkthrough execution, screenshot capture,
and DuckDB shadow sandbox 4-zone DDL compilation.
"""

from __future__ import annotations

import os
import shutil
import socket
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
    "01_wizard_step1_baseline.png",
    "02_wizard_step2_sources.png",
    "03_wizard_step3_evidence.png",
    "04_wizard_step4_discovery.png",
    "05_wizard_step5_graph.png",
    "06_wizard_step6_pipeline.png",
    "07_dv_schema_explorer.png",
    "08_settings_view.png",
]

MIN_SCREENSHOT_BYTES = 10_000


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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


def test_browser_e2e_walkthrough(tmp_path: Path) -> None:
    """Execute Playwright E2E walkthrough for the unified 6-step wizard on isolated ports."""
    e2e_screenshots_dir = WORKBENCH_DIR / "tests" / "e2e" / "screenshots"
    e2e_screenshots_dir.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure public datasets are staged
    fetcher = PublicDataFetcher(output_dir=REPO_ROOT / "data" / "public_am", mode="fixture")
    fetcher.fetch_all()

    # Execute genuine Playwright test via bun runner on isolated ephemeral ports & state dir
    bun_exe = shutil.which("bun")
    assert bun_exe is not None, "bun executable is required for browser E2E walkthrough"
    assert WORKBENCH_DIR.is_dir(), f"Workbench directory missing at {WORKBENCH_DIR}"

    api_port = _find_free_port()
    web_port = _find_free_port()
    while web_port == api_port:
        web_port = _find_free_port()

    state_dir = tmp_path / "state"
    studio_dir = tmp_path / "studio"
    state_dir.mkdir(parents=True, exist_ok=True)
    studio_dir.mkdir(parents=True, exist_ok=True)

    env = {
        **os.environ,
        "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH": str(CHROMIUM_BINARY),
        "OPENDV_E2E_API_PORT": str(api_port),
        "OPENDV_E2E_WEB_PORT": str(web_port),
        "OPENDV_BASE_URL": f"http://127.0.0.1:{web_port}",
        "OPENDV_API_URL": f"http://127.0.0.1:{api_port}",
        "OPENDV_STORAGE_ROOT": str(state_dir),
        "OPENDV_STATE_DIR": str(state_dir),
        "OPENDV_STUDIO_ROOT": str(studio_dir),
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
    try:
        subprocess.run(
            cmd,
            cwd=str(WORKBENCH_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=360,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise AssertionError(
            f"Playwright E2E failed (exit {exc.returncode}):\nSTDOUT:\n{exc.stdout}\nSTDERR:\n{exc.stderr}"
        ) from exc

    # Assert genuine screenshots produced by Playwright (>10KB each)
    for sc in EXPECTED_SCREENSHOTS:
        sc_path = e2e_screenshots_dir / sc
        assert sc_path.exists(), f"Expected screenshot {sc} missing in {e2e_screenshots_dir}"
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
