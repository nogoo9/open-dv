"""Tests for 125-perm MinHash table clustering (scale_200) and unified PII scanner."""

from __future__ import annotations

from pathlib import Path
import time

from opendv.models.sources import ColumnProfile, TableProfile
from opendv.sources.profiler import profile_source
from opendv.sources.clustering import (
    MAX_CLUSTER_SIZE,
    MIN_CLUSTER_SIZE,
    build_table_index_entries,
    cluster_tables,
    collapse_shard_name,
)
from opendv.sources.pii import (
    MASKED_PII_TOKEN,
    compute_shannon_entropy,
    detect_pii_in_column_name,
    detect_pii_in_value,
    is_valid_aba_routing,
    is_valid_luhn,
    mask_cell_value,
    mask_pii_text,
)
from tests.fixtures.scale_200 import build_scale_200_table_profiles


def _make_table(name: str, cols: list[tuple[str, str, str | None]]) -> TableProfile:
    return TableProfile(
        table_id=f"src:{name}",
        table_name=name,
        row_count_est=10,
        columns=[
            ColumnProfile(
                col_name=c,
                sql_type=t,
                nullable=True,
                is_pk=(idx == 0),
                pk_ord=1 if idx == 0 else None,
                fk_ref=fk,
                null_ratio=0.0,
                distinct_count_est=10,
                pii_tags=[],
                sample_values=[],
            )
            for idx, (c, t, fk) in enumerate(cols)
        ],
    )


def test_clustering_shard_collapse_small_catalog_and_bridge_split() -> None:
    """UAT-S3-01: Shard collapse, <10 single cluster, and >25 component splitting."""
    assert collapse_shard_name("trades_20240101") == "trades"
    assert collapse_shard_name("orders_0042") == "orders"
    assert collapse_shard_name("static_ref_v2") == "static_ref_v2"

    small = [_make_table(f"t_{i}", [("id", "INT", None), ("val", "VARCHAR", None)]) for i in range(6)]
    small_res = cluster_tables(small)
    assert len(small_res.clusters) == 1
    assert len(small_res.clusters[0]) == 6

    # Single connected component of 40 tables with a weak bridge between two 20-table halves
    large: list[TableProfile] = []
    for half in (0, 1):
        for i in range(20):
            t_name = f"grp{half}_tbl_{i:02d}"
            fk = "grp0_tbl_00.id" if (half == 1 and i == 0) else None
            cols = [(f"h{half}_col_{k}", "VARCHAR", fk if k == 0 else None) for k in range(8)]
            large.append(_make_table(t_name, cols))

    large_res = cluster_tables(large)
    assert all(len(c) <= MAX_CLUSTER_SIZE for c in large_res.clusters)
    assert all(len(c) >= MIN_CLUSTER_SIZE for c in large_res.clusters)


def test_scale_200_clustering_latency_bounds_and_determinism() -> None:
    """UAT-S3-01: 220 tables -> 11 clusters (10-25/cluster), deterministic, <300ms."""
    tables_ordered = build_scale_200_table_profiles()
    tables_shuffled = build_scale_200_table_profiles(shuffle_seed=42)

    t0 = time.perf_counter()
    res_ordered = cluster_tables(tables_ordered)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    res_shuffled = cluster_tables(tables_shuffled)

    assert len(tables_ordered) == 220
    assert len(res_ordered.clusters) == 11
    for cluster in res_ordered.clusters:
        assert MIN_CLUSTER_SIZE <= len(cluster) <= MAX_CLUSTER_SIZE
    assert res_ordered.table_to_cluster == res_shuffled.table_to_cluster
    assert elapsed_ms < 300.0

    entries = build_table_index_entries(tables_ordered, "scale_200")
    assert len(entries) == 220
    assert len({e.cluster_id for e in entries}) == 11


def test_pii_aba_routing_luhn_entropy_hex64_and_exemptions() -> None:
    """SCEN-08: Unified PII detection, Mod-10 ABA, Luhn, entropy>=4.2 & safe exemptions."""
    # Valid ABA in routing context triggers; synthetic seeds and bare 9-digit numbers do NOT
    assert is_valid_aba_routing("021000021") is True
    assert is_valid_aba_routing("202601015") is False
    assert is_valid_aba_routing("190001010") is False
    assert is_valid_aba_routing("000000000") is False

    assert "ABA_ROUTING" in detect_pii_in_value("ABA 021000021")
    assert detect_pii_in_value("Ref 021000021") == []
    assert detect_pii_in_value("ABA 202601015") == []
    assert mask_pii_text("ABA 021000021") == f"ABA {MASKED_PII_TOKEN}"

    # SSN, EIN, Email, Phone, IBAN, CC PAN (Luhn)
    assert "SSN" in detect_pii_in_value("123-45-6789")
    assert "EIN" in detect_pii_in_value("12-3456789")
    assert "EMAIL" in detect_pii_in_value("steward@am-example.com")
    assert "PHONE" in detect_pii_in_value("212-555-0199")
    assert "IBAN" in detect_pii_in_value("DE89370400440532013000")
    assert is_valid_luhn("4111-1111-1111-1111") is True
    assert "CC_PAN" in detect_pii_in_value("Card 4111-1111-1111-1111")
    assert detect_pii_in_value("Card 4111-1111-1111-1112") == []

    # 64-hex & Shannon entropy >= 4.2 with Open-DV HK & identifier/URI exemptions
    digest = "a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0"
    assert detect_pii_in_value(digest, col_name="hk_legal_entity") == []
    assert detect_pii_in_value(digest, col_name="hash_diff") == []
    assert "HEX_64" in detect_pii_in_value(digest, col_name="raw_payload")

    # High-entropy secret vs exempted snake_case / CamelCase / urn: / https://
    secret = "sK9$mP2xQ7rT4vW8zY1bN5cM3!"
    assert compute_shannon_entropy(secret) >= 4.2
    assert "HIGH_ENTROPY_SECRET" in detect_pii_in_value(secret)
    assert mask_cell_value(secret) == MASKED_PII_TOKEN

    for safe_ident in (
        "this_is_a_very_long_snake_case_identifier_name",
        "VeryLongCamelCaseClassNameHere",
        "urn:opendv:concept:legal_entity_with_long_name",
        "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity",
    ):
        assert detect_pii_in_value(safe_ident) == []

    assert detect_pii_in_column_name("beneficial_owner_tax_id")
    assert detect_pii_in_column_name("taxId") == ["PII_COLUMN:TAXID"]
    assert detect_pii_in_column_name("userTaxId") == ["PII_COLUMN:TAXID"]
    assert detect_pii_in_column_name("clientTaxId") == ["PII_COLUMN:TAXID"]
    assert detect_pii_in_column_name("custAcctNum") == ["PII_COLUMN:ACCTNUM"]
    assert detect_pii_in_column_name("accountNumber") == ["PII_COLUMN:ACCOUNTNUMBER"]
    assert detect_pii_in_column_name("holderName") == ["PII_COLUMN:HOLDERNAME"]
    assert detect_pii_in_column_name("creditCard") == ["PII_COLUMN:CREDITCARD"]
    assert detect_pii_in_column_name("cardNumber") == ["PII_COLUMN:CARDNUMBER"]
    assert detect_pii_in_column_name("syntax_id") == []
    assert detect_pii_in_column_name("syntaxId") == []
    assert detect_pii_in_column_name("database_id") == []
    assert detect_pii_in_column_name("databaseId") == []
    assert detect_pii_in_column_name("esg_rating_id") == []
    assert detect_pii_in_column_name("reporting_fiscal_year") == []
    assert detect_pii_in_column_name("agm_meeting_date") == []
    assert detect_pii_in_column_name("executing_broker_lei") == []
    assert detect_pii_in_column_name("counterparty_aba_raw") == ["PII_COLUMN:ABA"]
    assert detect_pii_in_column_name("clientTin") == ["PII_COLUMN:TIN"]
    assert detect_pii_in_column_name("user_acct_num") == ["PII_COLUMN:ACCT_NUM"]
    assert detect_pii_in_value("ALPHA_BRAVO_CHARLIE_DELTA_ECHO_FOXTROT_0123456789") == []
    assert mask_cell_value("normal_val", column_pii_tags=["PII_COLUMN:EMAIL"]) == MASKED_PII_TOKEN
    assert mask_cell_value(None, column_pii_tags=["PII_COLUMN:EMAIL"]) is None


def test_real_am_datasets_no_false_positive_pii_tags() -> None:
    """Regression: short PII column tokens never false-positive on real AM columns."""
    public_am = Path(__file__).resolve().parent.parent.parent / "data" / "public_am"

    esg_prof = profile_source(
        "src_esg_pii_check",
        "parquet",
        str(public_am / "am_esg_stewardship_proxy.parquet"),
    )
    esg_cols = {c.col_name: c for c in esg_prof.tables[0].columns}
    for non_pii in (
        "esg_rating_id",
        "rating_effective_date",
        "reporting_fiscal_year",
        "agm_meeting_date",
    ):
        assert esg_cols[non_pii].pii_tags == []
        assert all(v != MASKED_PII_TOKEN for v in esg_cols[non_pii].sample_values)

    trap_prof = profile_source(
        "src_trap_pii_check",
        "duckdb",
        str(public_am / "ds_cegar_12trap.duckdb"),
    )
    trap_tables = {t.table_name: t for t in trap_prof.tables}
    crm_cols = {c.col_name: c for c in trap_tables["trap_crm_accounts"].columns}
    ibor_cols = {c.col_name: c for c in trap_tables["trap_ibor_accounts"].columns}

    for non_pii_col in (crm_cols["risk_rating"], crm_cols["reporting_ccy"], ibor_cols["executing_broker_lei"]):
        assert non_pii_col.pii_tags == []
        assert all(v != MASKED_PII_TOKEN for v in non_pii_col.sample_values)

    for pii_col in ("beneficial_owner_tax_id", "stewardship_officer_email", "client_phone"):
        assert crm_cols[pii_col].pii_tags
        assert all(v == MASKED_PII_TOKEN for v in crm_cols[pii_col].sample_values if v is not None)


def test_clustering_skewed_split_pending_merge_and_empty_collapsed_name() -> None:
    """Hardening: skewed [7,7,16] split, smallest-first pending merge, and empty shard stem."""
    # [7, 7, 16] FK-linked shard groups -> splits to [16, 14]
    skewed: list[TableProfile] = []
    for i in range(7):
        skewed.append(
            _make_table(
                f"alpha_{20240100 + i}",
                [("id", "INT", None), ("val", "VARCHAR", "gamma_20240100.id" if i == 0 else None)],
            )
        )
    for i in range(7):
        skewed.append(
            _make_table(
                f"beta_{20240100 + i}",
                [("id", "INT", None), ("val", "VARCHAR", "gamma_20240100.id" if i == 0 else None)],
            )
        )
    for i in range(16):
        skewed.append(
            _make_table(f"gamma_{20240100 + i}", [("id", "INT", None), ("amt", "DOUBLE", None)])
        )

    skewed_res = cluster_tables(skewed)
    assert sorted(len(c) for c in skewed_res.clusters) == [14, 16]

    # Disconnected [5, 10, 24] shard groups -> pending [5] merges into [10] -> [15, 24]
    disconn: list[TableProfile] = []
    for prefix, count, col_prefix in (("p_small", 5, "sa"), ("q_mid", 10, "mb"), ("r_big", 24, "lc")):
        for i in range(count):
            disconn.append(
                _make_table(
                    f"{prefix}_{20240100 + i}",
                    [(f"{col_prefix}_k{j}", "VARCHAR", None) for j in range(6)],
                )
            )
    disconn_res = cluster_tables(disconn)
    assert sorted(len(c) for c in disconn_res.clusters) == [15, 24]

    # Empty shard-collapsed base name ("_20240101" -> "") inside >25 component never raises IndexError
    empty_stem = [
        _make_table(f"_{20240100 + i}", [("id", "INT", None), ("v", "VARCHAR", None)])
        for i in range(5)
    ] + [
        _make_table(f"other_{i:02d}", [("id", "INT", None), ("v", "VARCHAR", None)])
        for i in range(25)
    ]
    empty_res = cluster_tables(empty_stem)
    assert sum(len(c) for c in empty_res.clusters) == 30


def test_profile_source_masks_camelcase_taxid_and_preserves_syntaxid(tmp_path: Path) -> None:
    """Regression: profile_source masks CamelCase taxId/custAcctNum while leaving syntaxId intact."""
    csv_path = tmp_path / "camelcase_pii.csv"
    csv_path.write_text(
        "id,taxId,userTaxId,custAcctNum,syntaxId,syntax_id\n"
        "1,TX-998877,UT-1122,ACC-4455,SYN-1,SYN_2\n",
        encoding="utf-8",
    )
    prof = profile_source("p_taxid", "csv", str(csv_path))
    cols = {c.col_name: c for c in prof.tables[0].columns}

    for pii_col in ("taxId", "userTaxId", "custAcctNum"):
        assert cols[pii_col].pii_tags
        assert cols[pii_col].sample_values == [MASKED_PII_TOKEN]

    for safe_col in ("syntaxId", "syntax_id"):
        assert cols[safe_col].pii_tags == []
        assert all(v != MASKED_PII_TOKEN for v in cols[safe_col].sample_values)
