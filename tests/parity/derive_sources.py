"""Helper utilities for deriving wave-0 multi-format datasets for T16 parity tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import duckdb
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from opendv_sources.models.specs import SourceSpec

REPO_ROOT = Path(__file__).resolve().parents[2]
GCV_DIR = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"
WAVE0_DIR = GCV_DIR / "sources" / "wave0_cold_start"


def derive_format_sources(tmp_path: Path) -> dict[str, dict[str, SourceSpec]]:
    """Derive wave-0 CSVs into TSV, Parquet, JSONL, SQLite, DuckDB, and Inline."""
    crm_csv = WAVE0_DIR / "crm_customers.csv"
    erp_csv = WAVE0_DIR / "erp_orders.csv"

    conv = pacsv.ConvertOptions(strings_can_be_null=True, null_values=["", "NA", "null", "NULL"])
    t_crm = pacsv.read_csv(crm_csv, convert_options=conv)
    t_erp = pacsv.read_csv(erp_csv, convert_options=conv)

    # 1. TSV
    tsv_dir = tmp_path / "tsv"
    tsv_dir.mkdir(exist_ok=True)
    crm_tsv = tsv_dir / "crm_customers.tsv"
    erp_tsv = tsv_dir / "erp_orders.tsv"
    pacsv.write_csv(t_crm, crm_tsv, write_options=pacsv.WriteOptions(delimiter="\t"))
    pacsv.write_csv(t_erp, erp_tsv, write_options=pacsv.WriteOptions(delimiter="\t"))

    # 2. Parquet
    pq_dir = tmp_path / "parquet"
    pq_dir.mkdir(exist_ok=True)
    crm_pq = pq_dir / "crm_customers.parquet"
    erp_pq = pq_dir / "erp_orders.parquet"
    pq.write_table(t_crm, crm_pq)
    pq.write_table(t_erp, erp_pq)

    # 3. JSONL
    jl_dir = tmp_path / "jsonl"
    jl_dir.mkdir(exist_ok=True)
    crm_jl = jl_dir / "crm_customers.jsonl"
    erp_jl = jl_dir / "erp_orders.jsonl"
    crm_jl.write_text(
        "\n".join(json.dumps(r, default=str) for r in t_crm.to_pylist()) + "\n",
        encoding="utf-8",
    )
    erp_jl.write_text(
        "\n".join(json.dumps(r, default=str) for r in t_erp.to_pylist()) + "\n",
        encoding="utf-8",
    )

    # 4. SQLite
    sq_dir = tmp_path / "sqlite"
    sq_dir.mkdir(exist_ok=True)
    sq_db = sq_dir / "wave0.sqlite"
    with sqlite3.connect(sq_db) as sq_con:
        sq_con.execute(
            "CREATE TABLE crm_customers (customer_row_seq INT, customer_tax_id TEXT, "
            "legal_name TEXT, country_iso2 TEXT, account_segment TEXT, credit_limit_usd REAL, "
            "contact_email TEXT, tax_ssn_tin TEXT)"
        )
        sq_con.execute(
            "CREATE TABLE erp_orders (order_id TEXT, customer_tax_id TEXT, order_date TEXT, "
            "order_total_usd REAL, currency_code TEXT, order_status TEXT, promo_campaign_code TEXT)"
        )
        for r in t_crm.to_pylist():
            sq_con.execute(
                "INSERT INTO crm_customers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(str(v) if hasattr(v, "isoformat") else v for v in r.values()),
            )
        for r in t_erp.to_pylist():
            sq_con.execute(
                "INSERT INTO erp_orders VALUES (?, ?, ?, ?, ?, ?, ?)",
                tuple(str(v) if hasattr(v, "isoformat") else v for v in r.values()),
            )

    # 5. DuckDB
    ddb_dir = tmp_path / "duckdb"
    ddb_dir.mkdir(exist_ok=True)
    ddb_file = ddb_dir / "wave0.duckdb"
    with duckdb.connect(str(ddb_file)) as con:
        con.register("t_crm", t_crm)
        con.register("t_erp", t_erp)
        con.execute("CREATE TABLE crm_customers AS SELECT * FROM t_crm")
        con.execute("CREATE TABLE erp_orders AS SELECT * FROM t_erp")

    return {
        "csv": {
            "crm": SourceSpec(
                adapter="file",
                uri=str(crm_csv.resolve()),
                format="csv",
            ),
            "erp": SourceSpec(
                adapter="file",
                uri=str(erp_csv.resolve()),
                format="csv",
            ),
        },
        "tsv": {
            "crm": SourceSpec(
                adapter="file",
                uri=str(crm_tsv.resolve()),
                format="tsv",
            ),
            "erp": SourceSpec(
                adapter="file",
                uri=str(erp_tsv.resolve()),
                format="tsv",
            ),
        },
        "parquet": {
            "crm": SourceSpec(
                adapter="file",
                uri=str(crm_pq.resolve()),
                format="parquet",
            ),
            "erp": SourceSpec(
                adapter="file",
                uri=str(erp_pq.resolve()),
                format="parquet",
            ),
        },
        "jsonl": {
            "crm": SourceSpec(
                adapter="file",
                uri=str(crm_jl.resolve()),
                format="jsonl",
            ),
            "erp": SourceSpec(
                adapter="file",
                uri=str(erp_jl.resolve()),
                format="jsonl",
            ),
        },
        "sqlite": {
            "crm": SourceSpec(
                adapter="duckdb_attach",
                uri=str(sq_db.resolve()),
                format="sqlite",
                options={
                    "database": str(sq_db.resolve()),
                    "table": "crm_customers",
                },
            ),
            "erp": SourceSpec(
                adapter="duckdb_attach",
                uri=str(sq_db.resolve()),
                format="sqlite",
                options={
                    "database": str(sq_db.resolve()),
                    "table": "erp_orders",
                },
            ),
        },
        "duckdb": {
            "crm": SourceSpec(
                adapter="duckdb_attach",
                uri=str(ddb_file.resolve()),
                format="duckdb",
                options={
                    "database": str(ddb_file.resolve()),
                    "table": "crm_customers",
                },
            ),
            "erp": SourceSpec(
                adapter="duckdb_attach",
                uri=str(ddb_file.resolve()),
                format="duckdb",
                options={
                    "database": str(ddb_file.resolve()),
                    "table": "erp_orders",
                },
            ),
        },
        "inline": {
            "crm": SourceSpec(
                adapter="inline",
                inline_content=crm_csv.read_text(encoding="utf-8"),
                format="csv",
            ),
            "erp": SourceSpec(
                adapter="inline",
                inline_content=erp_csv.read_text(encoding="utf-8"),
                format="csv",
            ),
        },
    }
