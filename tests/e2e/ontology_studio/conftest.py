"""Shared Pytest Fixtures for Ontology Studio E2E Integration Test Suite (Track 6B).

Provides isolated environments, REST client, authentication headers, and domain source fixtures.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings

# Shared ephemeral RS256 test keypair for remote platform testing
_TEST_RSA_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_TEST_RSA_PUBLIC_KEY_PEM = (
    _TEST_RSA_PRIVATE_KEY.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode("utf-8")
)


def mint_test_jwt(
    subject: str = "user:alice",
    role: str = "admin",
    expires_in: float = 3600.0,
    issuer: str = "opendv-platform",
    audience: str = "opendv-api",
    headers: dict[str, Any] | None = None,
    key: Any = _TEST_RSA_PRIVATE_KEY,
    algorithm: str = "RS256",
) -> str:
    """Helper to mint RS256 JWT tokens for testing remote platform mode."""
    now = int(time.time())
    payload = {
        "sub": subject,
        "role": role,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": int(now + expires_in),
    }
    return jwt.encode(payload, key, algorithm=algorithm, headers=headers)


@pytest.fixture
def isolated_studio_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Isolate each test in a clean temporary workspace and studio root."""
    from opendv_core.schema_registry import get_default_schema_registry

    get_default_schema_registry()
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    studio_root = tmp_path / "studio_root"
    studio_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
    monkeypatch.setenv("OPENDV_JWT_PUBLIC_KEY", _TEST_RSA_PUBLIC_KEY_PEM)
    monkeypatch.delenv("OPENDV_TOKEN", raising=False)
    monkeypatch.delenv("OPENDV_STUDIO_TOKEN", raising=False)
    monkeypatch.delenv("OPENDV_WAIVER_KEY", raising=False)

    return workspace_root, studio_root


@pytest.fixture
def studio_client(isolated_studio_env: tuple[Path, Path]) -> TestClient:
    """Create a FastAPI TestClient configured for local standalone mode."""
    workspace_root, _ = isolated_studio_env
    settings = OpenDVSettings.load(
        workspace_root=workspace_root,
        state_dir=workspace_root / "state",
        is_server=True,
    )
    app = create_app(settings=settings)
    return TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")


@pytest.fixture
def auth_headers(studio_client: TestClient) -> dict[str, str]:
    """Initialize local standalone environment and return valid auth headers."""
    resp = studio_client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "wealth_mgmt", "enterprise_owner": "Global Wealth Architecture"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["local_token"]
    return {
        "Authorization": f"Bearer {token}",
        "X-OpenDV-Role": "admin",
    }


@pytest.fixture
def remote_auth_headers() -> dict[str, str]:
    """Return valid RS256 JWT auth headers for testing remote platform mode."""
    token = mint_test_jwt(subject="user:steward", role="admin")
    return {
        "Authorization": f"Bearer {token}",
    }


@pytest.fixture
def pe_source_fixtures(tmp_path: Path) -> dict[str, Path]:
    """Create synthetic tabular and unstructured evidence matching the Private Equity domain."""
    pe_dir = tmp_path / "pe_sources"
    pe_dir.mkdir(parents=True, exist_ok=True)

    # 1. Funds table
    funds_csv = pe_dir / "pe_funds.csv"
    funds_csv.write_text(
        "fund_id,fund_name,strategy,vintage_year,target_size_usd\n"
        "fund_001,Apex Buyout Fund IV,BUYOUT,2024,500000000\n"
        "fund_002,Apex Growth Tech Fund II,GROWTH_EQUITY,2025,250000000\n"
        "fund_003,Apex Secondaries Alpha,SECONDARIES,2023,300000000\n",
        encoding="utf-8",
    )

    # 2. Limited Partners table
    lps_csv = pe_dir / "pe_limited_partners.csv"
    lps_csv.write_text(
        "lp_id,lp_name,institution_type,country,tax_id\n"
        "lp_101,California Public Employees Retirement System,PENSION,USA,US-992812\n"
        "lp_102,Ontario Teachers Pension Plan,PENSION,CAN,CA-881230\n"
        "lp_103,Temasek Holdings,SOVEREIGN_WEALTH,SGP,SG-771239\n",
        encoding="utf-8",
    )

    # 3. Commitments link table
    commitments_csv = pe_dir / "pe_commitments.csv"
    commitments_csv.write_text(
        "commitment_id,fund_id,lp_id,committed_amount_usd,side_letter_flag\n"
        "comm_01,fund_001,lp_101,100000000,true\n"
        "comm_02,fund_001,lp_102,75000000,false\n"
        "comm_03,fund_002,lp_103,50000000,true\n",
        encoding="utf-8",
    )

    # 4. Portfolio Companies table
    companies_csv = pe_dir / "pe_portfolio_companies.csv"
    companies_csv.write_text(
        "portfolio_company_id,company_name,industry_sector,enterprise_value_usd,ebitda_usd\n"
        "port_501,Nordic Cloud Solutions AB,B2B_SAAS,120000000,15000000\n"
        "port_502,Valence Health IT LLC,HEALTHCARE_IT,85000000,9200000\n"
        "port_503,Berlin Fintech Payments GmbH,FINTECH,210000000,24000000\n",
        encoding="utf-8",
    )

    # 5. Unstructured text document (Side Letter)
    side_letter_md = pe_dir / "calpers_side_letter.md"
    side_letter_md.write_text(
        "# SIDE LETTER AGREEMENT\n\n"
        "This Side Letter Agreement is entered into by CalPERS "
        "('LimitedPartner') and Apex Capital Management ('Fund') regarding Apex Buyout Fund IV.\n\n"
        "The Depository Custodian Bank for escrow balances shall be State Street Bank & Trust.\n",
        encoding="utf-8",
    )

    return {
        "funds": funds_csv,
        "limited_partners": lps_csv,
        "commitments": commitments_csv,
        "portfolio_companies": companies_csv,
        "side_letter": side_letter_md,
    }


@pytest.fixture
def wm_source_fixtures(tmp_path: Path) -> dict[str, Path]:
    """Create synthetic tabular evidence matching the Wealth Management domain."""
    wm_dir = tmp_path / "wm_sources"
    wm_dir.mkdir(parents=True, exist_ok=True)

    parties_csv = wm_dir / "wm_parties.csv"
    parties_csv.write_text(
        "party_id,legal_name,party_type,domicile_country,total_exposure_usd\n"
        "pty_1,Acme Family Trust,TRUST,USA,45000000\n"
        "pty_2,Sir Arthur Wellesley,INDIVIDUAL,GBR,12000000\n"
        "pty_3,Zurich Global Holding AG,CORPORATION,CHE,95000000\n",
        encoding="utf-8",
    )

    customers_csv = wm_dir / "wm_customers.csv"
    customers_csv.write_text(
        "customer_id,party_id,customer_status,risk_profile,credit_limit_usd\n"
        "cust_1,pty_1,ACTIVE,CONSERVATIVE,5000000\n"
        "cust_2,pty_2,ACTIVE,AGGRESSIVE,2000000\n"
        "cust_3,pty_3,ACTIVE,MODERATE,25000000\n",
        encoding="utf-8",
    )

    accounts_csv = wm_dir / "wm_accounts.csv"
    accounts_csv.write_text(
        "account_id,customer_id,account_type,currency,custodian_bank\n"
        "acc_1,cust_1,DISCRETIONARY_MANAGED,USD,BNY Mellon\n"
        "acc_2,cust_2,ADVISORY,GBP,Barclays Wealth\n"
        "acc_3,cust_3,EXECUTION_ONLY,EUR,UBS Switzerland\n",
        encoding="utf-8",
    )

    portfolios_csv = wm_dir / "wm_portfolios.csv"
    portfolios_csv.write_text(
        "portfolio_id,account_id,portfolio_name,base_currency,cash_balance\n"
        "port_1,acc_1,Acme High Yield Growth,USD,1250000\n"
        "port_2,acc_2,Wellesley Sovereign Bonds,GBP,450000\n"
        "port_3,acc_3,Zurich Multi-Asset Alpha,EUR,8900000\n",
        encoding="utf-8",
    )

    instruments_csv = wm_dir / "wm_instruments.csv"
    instruments_csv.write_text(
        "instrument_id,portfolio_id,isin,instrument_name,asset_class,market_value_usd\n"
        "inst_1,port_1,US0378331005,Apple Inc Common Stock,EQUITY,340000\n"
        "inst_2,port_1,US912828ZG62,US Treasury Note 4.25%,FIXED_INCOME,800000\n"
        "inst_3,port_2,GB00BL68HJ26,UK Gilt 3.75%,FIXED_INCOME,450000\n",
        encoding="utf-8",
    )

    return {
        "parties": parties_csv,
        "customers": customers_csv,
        "accounts": accounts_csv,
        "portfolios": portfolios_csv,
        "instruments": instruments_csv,
    }
