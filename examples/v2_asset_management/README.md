# Open-DV V2 Asset Management Walkthrough (`data/public_am/`)

This example guides you through **Steps 1–7** of the Open-DV V2 pipeline using real public Asset Management fixtures checked in under [`data/public_am/`](../../data/public_am/) — from SEC EDGAR 13F holdings and FRED Treasury yields to EU Custody LEI Parquet drift, unstructured stewardship PDFs, and the 12-trap CEGAR verification suite (`ds_cegar_12trap.duckdb`).

## Included Fixtures (`data/public_am/`)

| Fixture | Format | Modeling & CEGAR Purpose |
| :--- | :--- | :--- |
| `am_sec_treasury_ibor.sqlite` *(optional generated SQLite DB)* + `sec_13f_holdings.csv`, `sec_security_master.csv`, `fred_treasury_rates.csv` | SQLite / CSV | Cold-start Asset Management wave (`Issuer`, `Security`, `Holding13F`, `YieldCurve`), FIBO Path A grounding (`>= 0.65`), and `3-Tier BKCC` rules (`LEI` / `ISIN` > `CIK` / `CUSIP`). |
| `am_esg_stewardship_proxy.parquet` & `wave2_eu_custody_lei_and_drift.parquet` | ZSTD Parquet | Multi-wave `S_k(t) -> S_k(t+1)` schema evolution (`EVO-01..08`), `sat_*_ext_*` companion satellites, `sal_*` crosswalks, and `SCEN-03` Beyond-FIBO empirical entities. |
| `wave2_ambiguous_otc_collateral.csv` & `wave2_bitemporal_nav_restatements.csv` | CSV | Polysemic disambiguation (`SCEN-06`), sparse optional FKs (`Alg A.4`), and bi-temporal restatement windowing (`BUG-05`). |
| `am_custody_and_voting_guide.pdf` & `am_stewardship_domain_notes.md` | PDF / Markdown | Flow B document maps, `1x` document-only rules (`SCEN-04`), hybrid attributes (`SCEN-05`), and PII isolation (`stewardship_officer_email` -> `sat_proxy_ballot_pii`, `SCEN-08`). |
| `ds_cegar_12trap.duckdb` | DuckDB | `12` deliberate Data Vault 2.0 modeling traps verified inside the 3-Loop CEGAR DuckDB prover (`V6`). |

## Quick End-to-End V2 CLI Walkthrough

Run from the `open-dv/` repository root:

```bash
# 1. Verify environment health & agent readiness
uv run opendv --json doctor

# 2. Initialize project seeded with the Multi-Asset IBOR starter pack
uv run opendv project init --project am_v2_demo --namespace asset_management --starter-pack multi_asset_ibor

# 3. Attach Wave-1 & Wave-2 real Asset Management sources (and unstructured notes)
uv run opendv source attach --project am_v2_demo --kind csv --uri data/public_am/sec_13f_holdings.csv
uv run opendv source attach --project am_v2_demo --kind csv --uri data/public_am/fred_treasury_rates.csv
uv run opendv source attach --project am_v2_demo --kind parquet --uri data/public_am/am_esg_stewardship_proxy.parquet
uv run opendv source attach --project am_v2_demo --kind pdf --uri data/public_am/am_custody_and_voting_guide.pdf
uv run opendv source note --project am_v2_demo --path data/public_am/am_stewardship_domain_notes.md

# 4. Run ontology discovery + Query Ask FIBO + Build 5-Zone VaultSchemaIR v2.0
uv run opendv ontology fibo-qa --query "Securities Holding Issuer Yield Curve" --top-k 5
uv run opendv ontology construct --project am_v2_demo --discovery-mode incremental_new --wait
uv run opendv vault build --project am_v2_demo --policy balanced --wait
uv run opendv --json vault verify --project am_v2_demo

# 5. Compile Step 7 SQL & dbt outputs and export portable .opendv.zip (core default)
uv run opendv --json storage compile --project am_v2_demo --target duckdb_local --verify-runtime
uv run opendv --json storage compile --project am_v2_demo --target dbt --dbt-dialect postgres
uv run opendv storage query --project am_v2_demo --sql "SELECT table_name FROM information_schema.tables" --limit 20
uv run opendv project export --project am_v2_demo --out artifacts/am_v2_demo.core.opendv.zip
```

## Web Workbench Mode

```bash
uv run opendv serve --host 127.0.0.1 --port 8420
```

Open **`http://127.0.0.1:8420`** to step through **Steps 1–7** (`Guided ⇄ Observatory`, `Budget Meter`), inspect the **5-Zone (`Z0–Z4`)** **DV Schema Explorer**, replay attempts in **Run Explorer**, and configure harnesses/secrets in **Settings**.
