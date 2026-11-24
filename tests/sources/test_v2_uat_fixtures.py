"""Real-data & diff E2E validation of the 6 generated V2 UAT fixtures in data/public_am/."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from opendv.sources.clustering import build_table_index_entries, cluster_tables  # noqa: E402
from opendv.sources.document_map import DocumentMapBuilder  # noqa: E402
from opendv.sources.profiler import preview_table, profile_source  # noqa: E402
from scripts.build_v2_uat_fixtures import build_all_v2_uat_fixtures  # noqa: E402

PUBLIC_AM = REPO_ROOT / "data" / "public_am"


def test_generated_6_v2_uat_fixtures_in_public_am() -> None:
    """UAT-FIX-01 & UAT-FIX-02: Verify all 6 generated fixtures via opendv.sources."""
    # 1. am_custody_and_voting_guide.pdf (6 pages)
    pdf_path = PUBLIC_AM / "am_custody_and_voting_guide.pdf"
    assert pdf_path.is_file()
    builder = DocumentMapBuilder()
    dmap, pages = builder.build_from_path(
        source_id="src_custody_pdf",
        path=pdf_path,
        doc_format="pdf",
    )
    assert dmap.page_count == 6
    assert len(dmap.outline) == 6
    assert dmap.pages[2].table_headers == ["ballot_id", "issuer_cusip", "vote_choice"]
    assert dmap.pages[3].is_scanned_or_low_density is True
    assert dmap.pages[3].has_images is True
    p5_phrase = "CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE"
    assert pages[4].count(p5_phrase) == 2

    # 2. wave2_eu_custody_lei_and_drift.parquet (240 rows x 7 cols)
    pq_prof = profile_source(
        "src_w2_eu",
        "parquet",
        str(PUBLIC_AM / "wave2_eu_custody_lei_and_drift.parquet"),
    )
    assert pq_prof.table_count == 1
    assert pq_prof.tables[0].row_count_est == 240
    assert len(pq_prof.tables[0].columns) == 7

    # 3. wave2_bitemporal_nav_restatements.csv (180 rows x 7 cols, 216.28 -> 245.0)
    bi_path = PUBLIC_AM / "wave2_bitemporal_nav_restatements.csv"
    bi_prof = profile_source("src_w2_bi", "csv", str(bi_path))
    assert bi_prof.tables[0].row_count_est == 180
    assert len(bi_prof.tables[0].columns) == 7
    bi_text = bi_path.read_text(encoding="utf-8")
    assert "216.28" in bi_text
    assert "245.0" in bi_text

    # 4. wave2_ambiguous_otc_collateral.csv (120 rows, ABA PII masked, uti_id & isda_master_id U=1.0)
    otc_path = PUBLIC_AM / "wave2_ambiguous_otc_collateral.csv"
    otc_prof = profile_source("src_w2_otc", "csv", str(otc_path))
    assert otc_prof.tables[0].row_count_est == 120
    otc_cols = {c.col_name: c for c in otc_prof.tables[0].columns}
    assert "ABA_ROUTING" in otc_cols["counterparty_aba_raw"].pii_tags
    assert otc_cols["counterparty_aba_raw"].sample_values == ["<MASKED_PII>"] * 5
    assert otc_cols["uti_id"].distinct_count_est == 120
    assert otc_cols["isda_master_id"].distinct_count_est == 120

    otc_page = preview_table(
        "src_w2_otc",
        "csv",
        str(otc_path),
        otc_prof.tables[0].table_name,
        offset=0,
        limit=10,
    )
    aba_idx = otc_page.columns.index("counterparty_aba_raw")
    assert all(r[aba_idx] == "<MASKED_PII>" for r in otc_page.rows)

    # 5. ds_cegar_12trap.duckdb (6 tables triggering TRAP-01..12)
    duck_path = PUBLIC_AM / "ds_cegar_12trap.duckdb"
    duck_prof = profile_source("src_trap12", "duckdb", str(duck_path))
    assert duck_prof.table_count == 6
    trap_tables = {t.table_name: t for t in duck_prof.tables}
    assert set(trap_tables) == {
        "trap_crm_accounts",
        "trap_ibor_accounts",
        "trap_ballots_mas",
        "trap_snapshots_churn",
        "trap_manager_eff",
        "trap_early_trades",
    }
    crm_cols = {c.col_name: c for c in trap_tables["trap_crm_accounts"].columns}
    assert len(crm_cols) >= 26
    for pii_col in ("beneficial_owner_tax_id", "stewardship_officer_email", "client_phone"):
        assert crm_cols[pii_col].pii_tags
        assert all(v == "<MASKED_PII>" for v in crm_cols[pii_col].sample_values if v is not None)

    con = duckdb.connect(str(duck_path), read_only=True)
    try:
        ibor_row = con.execute(
            "SELECT issuer_cik, issuer_legal_name FROM trap_ibor_accounts WHERE account_id='1001'"
        ).fetchone()
        assert ibor_row == ("0000909100", "Nordisk Suverän Fond")
        assert len(ibor_row[1]) == 20
        assert len(ibor_row[1].encode("utf-8")) == 21
    finally:
        con.close()

    # 6. prime_allocations_w2.csv (150 rows x 5 cols)
    alloc_prof = profile_source(
        "src_alloc_w2",
        "csv",
        str(PUBLIC_AM / "prime_allocations_w2.csv"),
    )
    assert alloc_prof.tables[0].row_count_est == 150
    assert len(alloc_prof.tables[0].columns) == 5


def test_wave1_to_wave2_profile_diff_and_fixture_determinism(tmp_path: Path) -> None:
    """E2E diff scenario: full Wave-1 vs Wave-1+Wave-2 clustering & deterministic fixtures."""
    w1_sqlite = profile_source(
        "w1_sqlite", "sqlite", str(PUBLIC_AM / "am_sec_treasury_ibor.sqlite")
    )
    w1_esg = profile_source(
        "w1_esg", "parquet", str(PUBLIC_AM / "am_esg_stewardship_proxy.parquet")
    )
    w1_tables = [*w1_sqlite.tables, *w1_esg.tables]
    assert len(w1_tables) == 7
    w1_cluster_res = cluster_tables(w1_tables)
    assert [len(c) for c in w1_cluster_res.clusters] == [7]

    w2_sources = (
        ("w2_eu", "parquet", "wave2_eu_custody_lei_and_drift.parquet"),
        ("w2_bi", "csv", "wave2_bitemporal_nav_restatements.csv"),
        ("w2_otc", "csv", "wave2_ambiguous_otc_collateral.csv"),
        ("w2_alloc", "csv", "prime_allocations_w2.csv"),
    )
    w2_profiles = [
        profile_source(src_id, kind, str(PUBLIC_AM / fname))
        for src_id, kind, fname in w2_sources
    ]
    for w2_prof in w2_profiles:
        assert w2_prof.schema_fingerprint != w1_sqlite.schema_fingerprint
        assert w2_prof.schema_fingerprint != w1_esg.schema_fingerprint

    w2_tables = [tbl for prof in w2_profiles for tbl in prof.tables]
    assert len(w2_tables) == 4
    combined_tables = [*w1_tables, *w2_tables]
    combined_cluster_res = cluster_tables(combined_tables)
    assert [len(c) for c in combined_cluster_res.clusters] == [11]
    assert len(combined_cluster_res.table_to_cluster) == 11

    w1_entries = build_table_index_entries(
        w1_tables,
        "wave1",
        badge_map={t.table_id: "UNCHANGED" for t in w1_tables},
        modeled_version="1.0.0",
    )
    w2_entries = build_table_index_entries(w2_tables, "wave2")
    assert all(e.badge == "UNCHANGED" for e in w1_entries)
    assert all(e.badge == "NEW" for e in w2_entries)

    run_a = build_all_v2_uat_fixtures(tmp_path / "run_a")
    run_b = build_all_v2_uat_fixtures(tmp_path / "run_b")
    for key in ("bitemporal_csv", "otc_csv", "allocations_csv", "parquet", "pdf"):
        sha_a = hashlib.sha256(run_a[key].read_bytes()).hexdigest()
        sha_b = hashlib.sha256(run_b[key].read_bytes()).hexdigest()
        assert sha_a == sha_b, f"Non-deterministic output for {key}"

    con_a = duckdb.connect(str(run_a["duckdb"]), read_only=True)
    con_b = duckdb.connect(str(run_b["duckdb"]), read_only=True)
    try:
        tbls_a = con_a.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
        tbls_b = con_b.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
        assert tbls_a == tbls_b
        assert len(tbls_a) == 6
        for (tbl_name,) in tbls_a:
            desc_a = con_a.execute(f'DESCRIBE "{tbl_name}"').fetchall()
            desc_b = con_b.execute(f'DESCRIBE "{tbl_name}"').fetchall()
            assert desc_a == desc_b, f"Schema mismatch for {tbl_name}"
            rows_a = sorted(
                con_a.execute(f'SELECT * FROM "{tbl_name}"').fetchall(),
                key=lambda r: tuple("" if v is None else str(v) for v in r),
            )
            rows_b = sorted(
                con_b.execute(f'SELECT * FROM "{tbl_name}"').fetchall(),
                key=lambda r: tuple("" if v is None else str(v) for v in r),
            )
            assert rows_a == rows_b, f"Row mismatch for {tbl_name}"
    finally:
        con_a.close()
        con_b.close()
