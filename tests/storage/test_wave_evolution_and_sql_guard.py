"""Phase V2-4 tests: Real AM Wave-1 -> Wave-2 evolution, EVO-04, regen, metadata_only, read-only SQL guard."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import duckdb
import pytest

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.project import EvolutionLockSpec
from opendv.models.vault_ir import (
    BusinessKeyPart,
    DecimalType,
    DraftField,
    LinkParticipantSpec,
)
from opendv.models.vault_ir_certified import (
    BronzeTableSpec,
    CertifiedHubSpec,
    CertifiedLinkSpec,
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    HashRecipeSpec,
    IRField,
    PhysicalLayoutHints,
    PitBridgeSpec,
    SchemaWaveDelta,
    SourceBindingSpec,
    StagingViewSpec,
)
from opendv.storage import (
    SQL_TARGET_IDS,
    DuckDbProverAdapter,
    SourceAttachment,
    SqlAstVerifier,
    SqlGlotVaultCompiler,
    TargetEvalGate,
    WaveMigrator,
    execute_readonly_query,
    materialize_duckdb_local,
    materialize_parquet_local,
    validate_readonly_query_sql,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
AM_SQLITE = REPO_ROOT / "data/public_am/am_sec_treasury_ibor.sqlite"
AM_PARQUET = REPO_ROOT / "data/public_am/am_esg_stewardship_proxy.parquet"
W2_CUSTODY = REPO_ROOT / "data/public_am/wave2_eu_custody_lei_and_drift.parquet"
W2_NAV = REPO_ROOT / "data/public_am/wave2_bitemporal_nav_restatements.csv"
W2_ALLOC = REPO_ROOT / "data/public_am/prime_allocations_w2.csv"


def _build_wave1_am_ir() -> CertifiedVaultIR:
    b_iss_cols = [
        IRField(field_id=1, name="cik", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="ticker", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=3, name="company_name", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=4, name="exchange", logical_type="UTF8_STRING", nullable=False, is_pii=False),
    ]
    b_esg_cols = [
        IRField(field_id=1, name="esg_rating_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="filer_cik", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=3, name="ticker", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=4, name="cusip", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=5, name="issuer_name", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=6, name="esg_provider_code", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=7, name="esg_overall_grade", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=8, name="environmental_score", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
        IRField(field_id=9, name="social_score", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
        IRField(field_id=10, name="governance_score", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
        IRField(field_id=11, name="controversy_flag", logical_type="BOOLEAN", nullable=False, is_pii=False),
        IRField(field_id=12, name="rating_effective_date", logical_type="DATE32", nullable=False, is_pii=False),
        IRField(field_id=13, name="carbon_disclosure_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=14, name="reporting_fiscal_year", logical_type="INT64", nullable=False, is_pii=False),
        IRField(field_id=15, name="scope1_tco2e", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
        IRField(field_id=16, name="scope2_tco2e", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
        IRField(field_id=17, name="scope3_tco2e", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
        IRField(field_id=18, name="carbon_intensity_tco2e_per_musd", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
        IRField(field_id=19, name="sbti_net_zero_status", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=20, name="proxy_ballot_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=21, name="agm_meeting_date", logical_type="DATE32", nullable=False, is_pii=False),
        IRField(field_id=22, name="proposal_category", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=23, name="proposal_title", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=24, name="management_recommendation", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=25, name="vote_cast", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=26, name="voted_shares", logical_type="INT64", nullable=False, is_pii=False),
        IRField(field_id=27, name="stewardship_policy_code", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=28, name="mandate_rule_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=29, name="mandate_policy_name", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=30, name="breach_severity", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=31, name="steward_review_required", logical_type="BOOLEAN", nullable=False, is_pii=False),
    ]
    r_iss = HashRecipeSpec(
        target_col="hk_issuer", recipe_kind="HUB_HK", bkcc_literal="GLOBAL",
        source_columns=["cik"], sort_alphabetically=False, key_columns=["cik"],
    )
    r_esg = HashRecipeSpec(
        target_col="hk_esg_rating", recipe_kind="HUB_HK", bkcc_literal="GLOBAL",
        source_columns=["esg_rating_id"], sort_alphabetically=False, key_columns=["esg_rating_id"],
    )
    hd_iss = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=["company_name", "exchange", "ticker"], sort_alphabetically=True, key_columns=["hk_issuer"],
    )
    hd_esg = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=["esg_overall_grade", "voted_shares"], sort_alphabetically=True, key_columns=["hk_esg_rating"],
    )

    return CertifiedVaultIR(
        project_id="am_demo",
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[
            BronzeTableSpec(table_name="bronze_issuers", source_id="am_db", source_table_id="am_db:issuers", columns=b_iss_cols),
            BronzeTableSpec(table_name="bronze_esg", source_id="am_esg", source_table_id="am_esg:am_esg_stewardship_proxy", columns=b_esg_cols),
        ],
        staging_views=[
            StagingViewSpec(view_name="stg_issuers", source_id="am_db", source_table_id="am_db:issuers", col_lineage_mappings={c.name: c.name for c in b_iss_cols}, hash_recipes=[r_iss, hd_iss], projected_fields=b_iss_cols),
            StagingViewSpec(view_name="stg_esg", source_id="am_esg", source_table_id="am_esg:am_esg_stewardship_proxy", col_lineage_mappings={c.name: c.name for c in b_esg_cols}, hash_recipes=[r_esg, hd_esg], projected_fields=b_esg_cols),
        ],
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_issuer", key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[BusinessKeyPart(table_id="am_db:issuers", col_name="cik", source_id="am_db", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
                source_bindings=[SourceBindingSpec(source_id="am_db", table_id="am_db:issuers", col_mappings={}, hash_recipe=r_iss)],
                columns=[
                    IRField(field_id=1, name="hk_issuer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="cik", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_iss, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_issuer"]),
            ),
            CertifiedHubSpec(
                hub_name="hub_esg_rating", key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[BusinessKeyPart(table_id="am_esg:am_esg_stewardship_proxy", col_name="esg_rating_id", source_id="am_esg", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
                source_bindings=[SourceBindingSpec(source_id="am_esg", table_id="am_esg:am_esg_stewardship_proxy", col_mappings={}, hash_recipe=r_esg)],
                columns=[
                    IRField(field_id=1, name="hk_esg_rating", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="esg_rating_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_esg, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_esg_rating"]),
            ),
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name="sat_issuer_core", sat_kind="STANDARD", parent_name="hub_issuer",
                source_id="am_db", source_table_id="am_db:issuers",
                attributes=[
                    DraftField(name="ticker", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    DraftField(name="company_name", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    DraftField(name="exchange", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                columns=[
                    IRField(field_id=1, name="hk_issuer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="ticker", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=6, name="company_name", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=7, name="exchange", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=hd_iss, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_issuer", "load_dts"]),
            ),
            CertifiedSatelliteSpec(
                sat_name="sat_esg_core", sat_kind="STANDARD", parent_name="hub_esg_rating",
                source_id="am_esg", source_table_id="am_esg:am_esg_stewardship_proxy",
                attributes=[
                    DraftField(name="esg_overall_grade", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    DraftField(name="voted_shares", logical_type="INT32", nullable=False, is_pii=False),
                ],
                columns=[
                    IRField(field_id=1, name="hk_esg_rating", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="esg_overall_grade", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=6, name="voted_shares", logical_type="INT32", nullable=False, is_pii=False),
                ],
                hash_recipe=hd_esg, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_esg_rating", "load_dts"]),
            ),
        ],
    )


def _build_wave2_am_ir(w1: CertifiedVaultIR) -> CertifiedVaultIR:
    """Evolve W1 -> W2: EVO-04 widen voted_shares INT32 -> INT64 + add sal_issuer + W2 custody/nav/alloc."""
    w2 = w1.model_copy(deep=True)
    w2.wave_number = 2

    # EVO-04 widening on sat_esg_core.voted_shares: INT32 -> INT64
    for s in w2.satellites:
        if s.sat_name == "sat_esg_core":
            s.attributes[1] = DraftField(name="voted_shares", logical_type="INT64", nullable=False, is_pii=False)
            s.columns[5] = IRField(field_id=6, name="voted_shares", logical_type="INT64", nullable=False, is_pii=False)

    # Wave 2 custody bronze & staging + sal_issuer crosswalk
    b_cust_cols = [
        IRField(field_id=1, name="custody_acct_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="lei_code", logical_type="UTF8_STRING", nullable=True, is_pii=False),
        IRField(field_id=3, name="isin", logical_type="UTF8_STRING", nullable=True, is_pii=False),
        IRField(field_id=4, name="swift_bic", logical_type="UTF8_STRING", nullable=True, is_pii=False),
        IRField(field_id=5, name="settlement_ccy", logical_type="UTF8_STRING", nullable=True, is_pii=False),
        IRField(field_id=6, name="custodian_fee_bps", logical_type=DecimalType(precision=18, scale=4), nullable=True, is_pii=False),
        IRField(field_id=7, name="effective_dts", logical_type="UTF8_STRING", nullable=True, is_pii=False),
    ]
    r_cust = HashRecipeSpec(
        target_col="hk_custody_acct", recipe_kind="HUB_HK", bkcc_literal="EU_CUSTODY",
        source_columns=["custody_acct_id"], sort_alphabetically=False, key_columns=["custody_acct_id"],
    )
    r_sal = HashRecipeSpec(
        target_col="hk_sal_issuer", recipe_kind="LINK_HK", bkcc_literal="GLOBAL",
        source_columns=["custody_acct_id", "lei_code"], sort_alphabetically=False,
        key_columns=["custody_acct_id", "lei_code"],
    )
    r_iss_lei = HashRecipeSpec(
        target_col="hk_issuer", recipe_kind="HUB_HK", bkcc_literal="GLOBAL",
        source_columns=["lei_code"], sort_alphabetically=False, key_columns=["lei_code"],
    )
    hd_ext = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=["swift_bic"], sort_alphabetically=True, key_columns=["hk_issuer"],
    )
    w2.bronze_tables.append(
        BronzeTableSpec(table_name="bronze_w2_custody", source_id="w2_custody", source_table_id="w2_custody:wave2_eu_custody_lei_and_drift", columns=b_cust_cols)
    )
    w2.staging_views.append(
        StagingViewSpec(
            view_name="stg_w2_custody", source_id="w2_custody",
            source_table_id="w2_custody:wave2_eu_custody_lei_and_drift",
            col_lineage_mappings={c.name: c.name for c in b_cust_cols},
            hash_recipes=[r_cust, r_iss_lei, r_sal, hd_ext], projected_fields=b_cust_cols,
        )
    )
    for h in w2.hubs:
        if h.hub_name == "hub_issuer":
            h.source_bindings.append(
                SourceBindingSpec(
                    source_id="w2_custody",
                    table_id="w2_custody:wave2_eu_custody_lei_and_drift",
                    col_mappings={"cik": "lei_code"},
                    hash_recipe=r_iss_lei,
                )
            )
    w2.hubs.append(
        CertifiedHubSpec(
            hub_name="hub_custody_acct", key_tier="TIER_3_LOCAL_SCOPED",
            business_keys=[BusinessKeyPart(table_id="w2_custody:wave2_eu_custody_lei_and_drift", col_name="custody_acct_id", source_id="w2_custody", key_tier="TIER_3_LOCAL_SCOPED", bkcc_value="EU_CUSTODY")],
            source_bindings=[SourceBindingSpec(source_id="w2_custody", table_id="w2_custody:wave2_eu_custody_lei_and_drift", col_mappings={}, hash_recipe=r_cust)],
            columns=[
                IRField(field_id=1, name="hk_custody_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                IRField(field_id=5, name="custody_acct_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
            ],
            hash_recipe=r_cust, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_custody_acct"]),
        )
    )
    w2.links.append(
        CertifiedLinkSpec(
            link_name="sal_issuer", zone_id="Z3", link_kind="SAME_AS",
            participants=[
                LinkParticipantSpec(hub_name="hub_issuer", role_name="MASTER", fk_col_name="m_hk_issuer", source_col_names=["lei_code"]),
                LinkParticipantSpec(hub_name="hub_custody_acct", role_name="DUPLICATE", fk_col_name="d_hk_custody_acct", source_col_names=["custody_acct_id"]),
            ],
            source_bindings=[
                SourceBindingSpec(
                    source_id="w2_custody", table_id="w2_custody:wave2_eu_custody_lei_and_drift",
                    col_mappings={"m_hk_issuer": "hk_issuer", "d_hk_custody_acct": "hk_custody_acct"},
                    hash_recipe=r_sal,
                )
            ],
            columns=[
                IRField(field_id=1, name="hk_sal_issuer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                IRField(field_id=2, name="m_hk_issuer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                IRField(field_id=3, name="d_hk_custody_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                IRField(field_id=4, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                IRField(field_id=5, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
            ],
            hash_recipe=r_sal, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_sal_issuer"]),
        )
    )
    # Companion sat_issuer_core_ext_v2
    w2.satellites.append(
        CertifiedSatelliteSpec(
            sat_name="sat_issuer_core_ext_v2", sat_kind="EXT", parent_name="hub_issuer",
            extends_sat_name="sat_issuer_core", companion_epoch=2,
            source_id="w2_custody", source_table_id="w2_custody:wave2_eu_custody_lei_and_drift",
            attributes=[DraftField(name="swift_bic", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
            columns=[
                IRField(field_id=1, name="hk_issuer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                IRField(field_id=5, name="swift_bic", logical_type="UTF8_STRING", nullable=True, is_pii=False),
            ],
            hash_recipe=HashRecipeSpec(
                target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
                source_columns=["swift_bic"], sort_alphabetically=True, key_columns=["hk_issuer"],
            ),
            physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_issuer", "load_dts"]),
        )
    )

    # Wave 2 bitemporal NAV restatements (W2_NAV: 180 rows)
    b_nav_cols = [
        IRField(field_id=1, name="fund_code", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="share_class", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=3, name="as_of_date", logical_type="DATE32", nullable=False, is_pii=False),
        IRField(field_id=4, name="applied_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
        IRField(field_id=5, name="nav_per_share", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
        IRField(field_id=6, name="aum_usd", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
        IRField(field_id=7, name="restatement_reason", logical_type="UTF8_STRING", nullable=False, is_pii=False),
    ]
    r_nav = HashRecipeSpec(
        target_col="hk_fund_nav", recipe_kind="HUB_HK", bkcc_literal="GLOBAL",
        source_columns=["fund_code", "share_class", "as_of_date"], sort_alphabetically=False,
        key_columns=["fund_code", "share_class", "as_of_date"],
    )
    hd_nav = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=["aum_usd", "nav_per_share", "restatement_reason"],
        sort_alphabetically=True, key_columns=["hk_fund_nav"],
    )
    w2.bronze_tables.append(
        BronzeTableSpec(table_name="bronze_w2_nav", source_id="w2_nav", source_table_id="w2_nav:wave2_bitemporal_nav_restatements", columns=b_nav_cols)
    )
    w2.staging_views.append(
        StagingViewSpec(
            view_name="stg_w2_nav", source_id="w2_nav",
            source_table_id="w2_nav:wave2_bitemporal_nav_restatements",
            col_lineage_mappings={c.name: c.name for c in b_nav_cols},
            hash_recipes=[r_nav, hd_nav], projected_fields=b_nav_cols,
        )
    )
    w2.hubs.append(
        CertifiedHubSpec(
            hub_name="hub_fund_nav", key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(table_id="w2_nav:wave2_bitemporal_nav_restatements", col_name="fund_code", source_id="w2_nav", key_tier="TIER_2_ENTERPRISE_SOR", bkcc_value="GLOBAL"),
                BusinessKeyPart(table_id="w2_nav:wave2_bitemporal_nav_restatements", col_name="share_class", source_id="w2_nav", key_tier="TIER_2_ENTERPRISE_SOR", bkcc_value="GLOBAL"),
                BusinessKeyPart(table_id="w2_nav:wave2_bitemporal_nav_restatements", col_name="as_of_date", source_id="w2_nav", key_tier="TIER_2_ENTERPRISE_SOR", bkcc_value="GLOBAL"),
            ],
            source_bindings=[SourceBindingSpec(source_id="w2_nav", table_id="w2_nav:wave2_bitemporal_nav_restatements", col_mappings={}, hash_recipe=r_nav)],
            columns=[
                IRField(field_id=1, name="hk_fund_nav", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                IRField(field_id=5, name="fund_code", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                IRField(field_id=6, name="share_class", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                IRField(field_id=7, name="as_of_date", logical_type="DATE32", nullable=False, is_pii=False),
            ],
            hash_recipe=r_nav, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_fund_nav"]),
        )
    )
    w2.satellites.append(
        CertifiedSatelliteSpec(
            sat_name="sat_nav_bitemp", sat_kind="STANDARD", parent_name="hub_fund_nav",
            source_id="w2_nav", source_table_id="w2_nav:wave2_bitemporal_nav_restatements",
            is_bitemporal=True, applied_dts_col="applied_dts",
            attributes=[
                DraftField(name="nav_per_share", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
                DraftField(name="aum_usd", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
                DraftField(name="restatement_reason", logical_type="UTF8_STRING", nullable=False, is_pii=False),
            ],
            columns=[
                IRField(field_id=1, name="hk_fund_nav", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                IRField(field_id=5, name="applied_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                IRField(field_id=6, name="nav_per_share", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
                IRField(field_id=7, name="aum_usd", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
                IRField(field_id=8, name="restatement_reason", logical_type="UTF8_STRING", nullable=False, is_pii=False),
            ],
            hash_recipe=hd_nav, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_fund_nav", "load_dts"]),
        )
    )
    w2.pit_bridges.append(
        PitBridgeSpec(
            object_name="pit_nav", object_kind="PIT", root_hub="hub_fund_nav",
            target_satellites_or_links=["sat_nav_bitemp"],
            grain_cols=["hk_fund_nav", "snapshot_dts"],
        )
    )

    # Wave 2 prime allocations MAS (W2_ALLOC: 150 rows)
    b_alloc_cols = [
        IRField(field_id=1, name="block_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="allocation_seq", logical_type="INT32", nullable=False, is_pii=False),
        IRField(field_id=3, name="fund_account", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=4, name="shares_allocated", logical_type="INT64", nullable=False, is_pii=False),
        IRField(field_id=5, name="avg_price_dec", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
    ]
    r_blk = HashRecipeSpec(
        target_col="hk_trade_block", recipe_kind="HUB_HK", bkcc_literal="GLOBAL",
        source_columns=["block_id"], sort_alphabetically=False, key_columns=["block_id"],
    )
    hd_alloc = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=["allocation_seq", "avg_price_dec", "fund_account", "shares_allocated"],
        sort_alphabetically=True, key_columns=["hk_trade_block"],
    )
    w2.bronze_tables.append(
        BronzeTableSpec(table_name="bronze_w2_alloc", source_id="w2_alloc", source_table_id="w2_alloc:prime_allocations_w2", columns=b_alloc_cols)
    )
    w2.staging_views.append(
        StagingViewSpec(
            view_name="stg_w2_alloc", source_id="w2_alloc",
            source_table_id="w2_alloc:prime_allocations_w2",
            col_lineage_mappings={c.name: c.name for c in b_alloc_cols},
            hash_recipes=[r_blk, hd_alloc], projected_fields=b_alloc_cols,
        )
    )
    w2.hubs.append(
        CertifiedHubSpec(
            hub_name="hub_trade_block", key_tier="TIER_1_GLOBAL_STANDARD",
            business_keys=[BusinessKeyPart(table_id="w2_alloc:prime_allocations_w2", col_name="block_id", source_id="w2_alloc", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
            source_bindings=[SourceBindingSpec(source_id="w2_alloc", table_id="w2_alloc:prime_allocations_w2", col_mappings={}, hash_recipe=r_blk)],
            columns=[
                IRField(field_id=1, name="hk_trade_block", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                IRField(field_id=5, name="block_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
            ],
            hash_recipe=r_blk, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_trade_block"]),
        )
    )
    w2.satellites.append(
        CertifiedSatelliteSpec(
            sat_name="mas_allocations", sat_kind="MAS", parent_name="hub_trade_block",
            source_id="w2_alloc", source_table_id="w2_alloc:prime_allocations_w2",
            child_dependent_keys=["allocation_seq"],
            attributes=[
                DraftField(name="fund_account", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                DraftField(name="shares_allocated", logical_type="INT64", nullable=False, is_pii=False),
                DraftField(name="avg_price_dec", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
            ],
            columns=[
                IRField(field_id=1, name="hk_trade_block", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                IRField(field_id=5, name="allocation_seq", logical_type="INT32", nullable=False, is_pii=False),
                IRField(field_id=6, name="fund_account", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                IRField(field_id=7, name="shares_allocated", logical_type="INT64", nullable=False, is_pii=False),
                IRField(field_id=8, name="avg_price_dec", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
            ],
            hash_recipe=hd_alloc, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_trade_block", "load_dts"]),
        )
    )

    w2.wave_delta = SchemaWaveDelta(
        from_wave=1, to_wave=2,
        added_entities=["hub_custody_acct", "sal_issuer", "hub_fund_nav", "hub_trade_block", "pit_nav"],
        added_sat_extensions=["sat_issuer_core_ext_v2"],
        locked_entities=["hub_issuer", "hub_esg_rating", "sat_issuer_core", "sat_esg_core"],
    )
    return w2


def test_real_am_wave1_wave2_incremental_migration_parity_and_disposable_regen(tmp_path: Path) -> None:
    """E2E real data W1 -> W2 materialization, EVO-04 widening, V001+V002 == full_schema_w2.sql, and disposable regen."""
    assert AM_SQLITE.exists() and AM_PARQUET.exists() and W2_CUSTODY.exists()
    assert W2_NAV.exists() and W2_ALLOC.exists()
    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)

    target_dir = tmp_path / "targets/duckdb_local"
    w1_att = [
        SourceAttachment(source_id="am_db", kind="sqlite", path=AM_SQLITE),
        SourceAttachment(source_id="am_esg", kind="parquet", path=AM_PARQUET),
    ]
    r1 = materialize_duckdb_local(w1, target_dir, w1_att)
    assert r1.verified is True and r1.rows_loaded == 80  # 50 issuers + 30 esg rows

    db_path = target_dir / "am_demo.duckdb"
    res_w1 = execute_readonly_query(db_path, "SELECT COUNT(*) AS cnt FROM raw_vault.hub_issuer")
    assert res_w1.rows[0][0] == 52  # 50 real + 2 Zero-Keys

    # Incremental Wave 2 migration preserving Wave 1 rows (custody 240 + nav 180 + alloc 150 = 570)
    w2_att = [
        *w1_att,
        SourceAttachment(source_id="w2_custody", kind="parquet", path=W2_CUSTODY),
        SourceAttachment(source_id="w2_nav", kind="csv", path=W2_NAV),
        SourceAttachment(source_id="w2_alloc", kind="csv", path=W2_ALLOC),
    ]
    r2 = materialize_duckdb_local(w2, target_dir, w2_att, prior_ir=w1)
    assert r2.verified is True and r2.rows_loaded == 570

    res_w2_iss = execute_readonly_query(db_path, "SELECT COUNT(*) AS cnt FROM raw_vault.hub_issuer")
    assert int(res_w2_iss.rows[0][0]) >= 52  # type: ignore[arg-type]
    res_w2_cust = execute_readonly_query(db_path, "SELECT COUNT(*) AS cnt FROM raw_vault.hub_custody_acct")
    assert res_w2_cust.rows[0][0] == 242  # 240 real + 2 Zero-Keys

    assert execute_readonly_query(db_path, "SELECT COUNT(*) FROM raw_vault.hub_fund_nav").rows[0][0] == 86
    assert execute_readonly_query(db_path, "SELECT COUNT(*) FROM raw_vault.sat_nav_bitemp").rows[0][0] == 87
    assert execute_readonly_query(db_path, "SELECT COUNT(*) FROM business_vault.pit_nav").rows[0][0] == 170
    assert execute_readonly_query(db_path, "SELECT COUNT(*) FROM raw_vault.hub_trade_block").rows[0][0] == 52
    assert execute_readonly_query(db_path, "SELECT COUNT(*) FROM raw_vault.mas_allocations").rows[0][0] == 151

    # Verify bitemporal restatements + PIT pointer resolution without 2x Cartesian duplication
    nav_hist = execute_readonly_query(
        db_path,
        "SELECT s.applied_dts, s.nav_per_share FROM raw_vault.hub_fund_nav AS h "
        "INNER JOIN raw_vault.sat_nav_bitemp AS s ON s.hk_fund_nav = h.hk_fund_nav "
        "WHERE h.fund_code = 'AM-CORE-EQ' AND h.share_class = 'INST_A' AND h.as_of_date = DATE '2026-10-20' "
        "ORDER BY s.applied_dts ASC",
    )
    assert [r[1] for r in nav_hist.rows] == [216.28, 245.0, 156.25]

    pit_res = execute_readonly_query(
        db_path,
        "SELECT p.snapshot_dts, s.nav_per_share FROM business_vault.pit_nav AS p "
        "INNER JOIN raw_vault.hub_fund_nav AS h ON h.hk_fund_nav = p.hk_fund_nav "
        "INNER JOIN raw_vault.sat_nav_bitemp AS s "
        "  ON s.hk_fund_nav = p.sat_nav_bitemp_hk AND s.load_dts = p.sat_nav_bitemp_load_dts "
        "  AND s.applied_dts = p.sat_nav_bitemp_applied_dts "
        "WHERE h.fund_code = 'AM-CORE-EQ' AND h.share_class = 'INST_A' AND h.as_of_date = DATE '2026-10-20' "
        "ORDER BY p.snapshot_dts ASC",
    )
    assert len(pit_res.rows) == 4  # 3 applied_dts snapshots + 1 hub.load_dts, zero Cartesian duplicates
    assert [r[1] for r in pit_res.rows] == [216.28, 245.0, 156.25, 156.25]

    # Verify EVO-04 widened voted_shares INTEGER -> BIGINT in place without dropping Z2
    col_type_q = execute_readonly_query(
        db_path,
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = 'raw_vault' AND table_name = 'sat_esg_core' AND column_name = 'voted_shares'",
    )
    assert col_type_q.rows == [["BIGINT"]]

    # Disposable targets/ deletion & byte-identical regeneration (UAT-S7-01)
    compiler = SqlGlotVaultCompiler("duckdb_local")
    dir_a = tmp_path / "regen_a"
    b1 = compiler.compile(w2, prior_ir=w1, output_dir=dir_a)
    shutil.rmtree(dir_a)
    b2 = compiler.compile(w2, prior_ir=w1, output_dir=dir_a)
    assert b1.files == b2.files

    # Parquet materialization on real Wave-1 + Wave-2 files (80 + 240 + 180 + 150 = 650)
    pq_dir = tmp_path / "targets/parquet"
    pr = materialize_parquet_local(w2, pq_dir, w2_att)
    assert pr.verified is True and pr.rows_loaded == 650

    # DuckDbProverAdapter().prove() succeeds on Wave-2 AM IR including sal_issuer with col_mappings
    w2_rep = DuckDbProverAdapter().prove(w2, [])
    assert len(w2_rep.dyn_results) == 15
    assert all(w2_rep.dyn_results.values()), (
        {k: v for k, v in w2_rep.dyn_results.items() if not v},
        w2_rep.counterexamples,
    )
    assert w2_rep.ast_violations == []


def test_wave_migrator_evo04_across_all_8_dialects_and_zero_z2_drop() -> None:
    """Verify WaveMigrator emits EVO-04 exp.Alter on 7 SQL dialects vs sat_*_ext_v2 on ClickHouse, 0 Z2 drops."""
    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    migrator = WaveMigrator()
    verifier = SqlAstVerifier()

    for tid in SQL_TARGET_IDS:
        stmts = migrator.compile_migration(w1, w2, tid)
        sql = ";\n".join(stmts) + ";"
        assert verifier.verify_sql(sql, tid, ir=w2) == []
        assert "DROP TABLE raw_vault" not in sql
        assert "TRUNCATE" not in sql.upper()
        if tid == "clickhouse":
            assert "ALTER COLUMN" not in sql
            assert "sat_esg_core_ext_v2" in sql
        else:
            assert "ALTER COLUMN voted_shares" in sql


def test_metadata_only_binary_absent_and_missing_file_error(tmp_path: Path) -> None:
    """metadata_only=True creates Zero/Ghost schemas only; metadata_only=False on missing file raises SOURCE_BINARY_ABSENT."""
    w1 = _build_wave1_am_ir()
    missing_att = [SourceAttachment(source_id="am_db", kind="sqlite", path=tmp_path / "missing.sqlite")]

    with pytest.raises(OpenDVError) as exc_abs:
        materialize_duckdb_local(w1, tmp_path / "fail_dir", missing_att, metadata_only=False)
    assert exc_abs.value.error_code == ErrorCode.SOURCE_BINARY_ABSENT

    meta_dir = tmp_path / "meta_only"
    res = materialize_duckdb_local(w1, meta_dir, missing_att, metadata_only=True)
    assert res.verified is True and res.rows_loaded == 0
    q = execute_readonly_query(meta_dir / "am_demo.duckdb", "SELECT COUNT(*) FROM raw_vault.hub_issuer")
    assert q.rows[0][0] == 2  # -1 and -2 Zero-Keys only

    pq_meta = tmp_path / "pq_meta"
    pq_res = materialize_parquet_local(w1, pq_meta, missing_att, metadata_only=True)
    assert pq_res.verified is True and pq_res.rows_loaded == 0


def test_readonly_query_ast_guard_blocks_all_forbidden_constructs(tmp_path: Path) -> None:
    """Sandboxed read-only storage/query allows SELECT/WITH and blocks all write/IO/multi-stmt injections."""
    w1 = _build_wave1_am_ir()
    out = tmp_path / "ro_db"
    materialize_duckdb_local(w1, out, metadata_only=True)
    db_path = out / "am_demo.duckdb"

    ok = execute_readonly_query(
        db_path,
        "WITH c AS (SELECT COUNT(*) AS n FROM raw_vault.hub_issuer) SELECT n FROM c",
    )
    assert ok.rows == [[2]]

    ok_info = execute_readonly_query(
        db_path,
        "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = 'raw_vault'",
    )
    assert int(ok_info.rows[0][0]) > 0  # type: ignore[arg-type]

    # Standard DuckDB read-only SQL functions & expressions must pass AST guard & execute cleanly
    standard_readonly_queries = [
        "SELECT CASE WHEN bkcc = 'GLOBAL' THEN 1 ELSE 0 END FROM raw_vault.hub_issuer",
        "SELECT DATE_TRUNC('day', load_dts) FROM raw_vault.hub_issuer",
        "SELECT DATE_DIFF('day', DATE '2026-01-01', DATE '2026-01-02')",
        "SELECT LOWER(HEX(hk_issuer)) FROM raw_vault.hub_issuer",
        "SELECT STRFTIME(load_dts AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') FROM raw_vault.hub_issuer",
        "SELECT STRING_AGG(cik, ',') FROM raw_vault.hub_issuer",
        "SELECT COALESCE(NULLIF(bkcc, ''), 'GLOBAL'), CAST(1 AS BIGINT), TRY_CAST('42' AS INT) FROM raw_vault.hub_issuer",
        "SELECT SUBSTR(cik, 1, 2), SUBSTRING(cik, 1, 2), TRIM(cik), REPLACE(cik, '-', '') FROM raw_vault.hub_issuer",
        "SELECT ARRAY_AGG(cik), LISTAGG(cik, ','), COUNT_IF(bkcc = 'GLOBAL') FROM raw_vault.hub_issuer",
        "SELECT ARRAY_AGG(load_dts) FROM raw_vault.hub_issuer",
        "SELECT TIMETZ '12:00:00+00'",
        "SELECT REGEXP_MATCHES(cik, '^0') FROM raw_vault.hub_issuer",
    ]
    for ro_q in standard_readonly_queries:
        validate_readonly_query_sql(ro_q)
        ro_res = execute_readonly_query(db_path, ro_q)
        assert ro_res.row_count >= 1, ro_q

    # Duplicate projected column names must preserve distinct values & UTC TIMESTAMPTZ formatting
    dup_num = execute_readonly_query(db_path, "SELECT 1 AS x, 2 AS x")
    assert dup_num.columns == ["x", "x"]
    assert dup_num.rows == [[1, 2]]

    dup_cik = execute_readonly_query(
        db_path, "SELECT cik, 'DIFF' AS cik FROM raw_vault.hub_issuer ORDER BY cik LIMIT 1"
    )
    assert dup_cik.columns == ["cik", "cik"]
    assert len(dup_cik.rows) == 1
    assert dup_cik.rows[0][1] == "DIFF" and dup_cik.rows[0][0] != "DIFF"

    dup_dts = execute_readonly_query(
        db_path,
        "SELECT h.load_dts, s.load_dts FROM raw_vault.hub_issuer AS h "
        "INNER JOIN (SELECT TIMESTAMPTZ '2026-10-21 09:30:00+02' AS load_dts) AS s ON TRUE LIMIT 1",
    )
    assert dup_dts.columns == ["load_dts", "load_dts"]
    assert len(dup_dts.rows) == 1
    assert dup_dts.rows[0][0].endswith("Z")  # type: ignore[union-attr]
    assert dup_dts.rows[0][1] == "2026-10-21T07:30:00.000000Z"
    assert dup_dts.rows[0][0] != dup_dts.rows[0][1]

    agg_dts = execute_readonly_query(db_path, "SELECT ARRAY_AGG(load_dts) AS dts_arr FROM raw_vault.hub_issuer")
    assert agg_dts.columns == ["dts_arr"]
    assert agg_dts.row_count == 1
    assert isinstance(agg_dts.rows[0][0], str) and agg_dts.rows[0][0].startswith("[")

    bad_queries = [
        "",
        "DROP TABLE raw_vault.hub_issuer",
        "INSERT INTO raw_vault.hub_issuer VALUES (NULL, 'X', NOW(), 'X', 'X')",
        "UPDATE raw_vault.hub_issuer SET bkcc = 'X'",
        "DELETE FROM raw_vault.hub_issuer",
        "ALTER TABLE raw_vault.hub_issuer ADD COLUMN x VARCHAR",
        "CREATE TABLE evil (a INT)",
        "SELECT * FROM read_csv('secret.csv')",
        "SELECT * FROM read_parquet('secret.parquet')",
        "SELECT * FROM read_json('secret.json')",
        "SELECT * FROM glob('*')",
        "SELECT * FROM parquet_scan('a.parquet')",
        "SELECT * FROM parquet_metadata('a.parquet')",
        "SELECT * FROM sniff_csv('a.csv')",
        "SELECT current_setting('access_mode')",
        "SELECT getenv('PATH')",
        "SELECT * FROM duckdb_secrets()",
        "SELECT * FROM duckdb_databases",
        "SELECT * FROM pragma_database_list",
        "SELECT * FROM duckdb_tables",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM sqlite_temp_master",
        "SELECT * FROM main.sqlite_master",
        "SELECT * FROM system.main.duckdb_databases",
        "SELECT * FROM system.tables",
        'SELECT * FROM "duckdb_databases"',
        "SELECT * FROM read_csv",
        "SELECT * FROM write_parquet",
        "SELECT * FROM iceberg_snapshots",
        "SELECT * FROM delta_tables",
        "SELECT * FROM s3_buckets",
        "SELECT * FROM httpfs_status",
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM pg_tables",
        "SELECT * FROM pg_views",
        "SELECT * FROM pg_settings",
        "SELECT * FROM pg_class",
        "SELECT * FROM pg_namespace",
        "SELECT * FROM pg_attribute",
        "SELECT * FROM pg_type",
        "SELECT * FROM pg_constraint",
        "WITH x AS (SELECT * FROM pg_tables) SELECT * FROM x",
        "SELECT * INTO temp_tbl FROM raw_vault.hub_issuer",
        "PRAGMA database_list",
        "ATTACH 'other.db' AS o",
        "COPY raw_vault.hub_issuer TO 'out.csv'",
        "SELECT * FROM 'secret.parquet'",
        "SELECT 1; SELECT 2",
        "SELECT 1;",
    ]
    for bad in bad_queries:
        with pytest.raises(OpenDVError) as exc:
            validate_readonly_query_sql(bad)
        assert exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT, bad

    for view_q in (
        "SELECT * FROM duckdb_databases",
        "SELECT * FROM pragma_database_list",
        "SELECT * FROM main.sqlite_master",
        "SELECT * FROM system.tables",
        "SELECT * FROM pg_tables",
        "SELECT * FROM pg_views",
        "SELECT * FROM pg_settings",
        "WITH x AS (SELECT * FROM pg_tables) SELECT * FROM x",
    ):
        with pytest.raises(OpenDVError) as exc_exec:
            execute_readonly_query(db_path, view_q)
        assert exc_exec.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT, view_q


def test_readonly_query_composite_constructors_and_quoted_file_targets(tmp_path: Path) -> None:
    """Allow STRUCT_PACK/MAP/ARRAY constructors and quoted schema.table; reject quoted/compound file targets."""
    w1 = _build_wave1_am_ir()
    out = tmp_path / "ro_comp"
    materialize_duckdb_local(w1, out, metadata_only=True)
    db_path = out / "am_demo.duckdb"

    for ok_q in (
        "SELECT STRUCT_PACK(ts := load_dts) AS v FROM raw_vault.hub_issuer LIMIT 1",
        "SELECT {'ts': load_dts} AS v FROM raw_vault.hub_issuer LIMIT 1",
        "SELECT MAP([1], [load_dts]) AS v FROM raw_vault.hub_issuer LIMIT 1",
        "SELECT MAP {1: load_dts} AS v FROM raw_vault.hub_issuer LIMIT 1",
        "SELECT [load_dts] AS v FROM raw_vault.hub_issuer LIMIT 1",
        "SELECT ARRAY_AGG(DISTINCT load_dts) AS v FROM raw_vault.hub_issuer",
        (
            "SELECT h.load_dts AS c, [h.load_dts] AS c, STRUCT_PACK(ts := h.load_dts) AS c, "
            "MAP([1], [h.load_dts]) AS c, CAST(NULL AS TIMESTAMPTZ) AS c, "
            "CAST(NULL AS TIMESTAMPTZ[]) AS c, NULL AS c FROM raw_vault.hub_issuer AS h LIMIT 1"
        ),
        'SELECT COUNT(*) FROM "raw_vault"."hub_issuer"',
    ):
        res = execute_readonly_query(db_path, ok_q)
        assert res.row_count == 1, ok_q

    for ok_empty_q in (
        "SELECT * FROM \"raw_vault\".\"hub_issuer\" WHERE cik = 'data.bin'",
        'SELECT * FROM "raw_vault"."hub_issuer" AS "data.bin" WHERE cik = \'data.bin\'',
    ):
        validate_readonly_query_sql(ok_empty_q)
        res = execute_readonly_query(db_path, ok_empty_q)
        assert res.row_count == 0, ok_empty_q

    for bad_file_q in (
        "SELECT * FROM 'secret.csv.gz'",
        'SELECT * FROM "secret.csv.gz"',
        'SELECT * FROM "secret.ndjson"',
        "SELECT * FROM 'secret.txt'",
        'SELECT * FROM "dir/secret"',
        "SELECT * FROM 'dir\\secret'",
        "SELECT * FROM 'secret.any_ext'",
        'SELECT * FROM (SELECT 1) AS a, \'data.bin\' AS "data.bin"',
        "SELECT * FROM (SELECT 1) AS a, 'data.bin'",
    ):
        with pytest.raises(OpenDVError) as exc:
            validate_readonly_query_sql(bad_file_q)
        assert exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT, bad_file_q


def test_materialize_duckdb_and_parquet_sources_with_timestamptz_and_composites(tmp_path: Path) -> None:
    """Verify materialize_duckdb_local and materialize_parquet_local handle tz-aware DuckDB & Parquet without pytz/tzdata."""
    from datetime import UTC, datetime

    import pyarrow as pa
    import pyarrow.parquet as pq

    from opendv.models.vault_ir import ArrayType, MapType

    cols = [
        IRField(field_id=1, name="item_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="ts_utc", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
        IRField(field_id=3, name="ts_arr", logical_type=ArrayType(element_type="TIMESTAMP_UTC_US"), nullable=True, is_pii=False),
        IRField(field_id=4, name="ts_map", logical_type=MapType(key_type="UTF8_STRING", value_type="TIMESTAMP_UTC_US"), nullable=True, is_pii=False),
    ]
    hk_r = HashRecipeSpec(
        target_col="hk_item",
        recipe_kind="HUB_HK",
        bkcc_literal="GLOBAL",
        source_columns=["item_id"],
        sort_alphabetically=False,
        key_columns=["item_id"],
    )
    hd_r = HashRecipeSpec(
        target_col="hash_diff",
        recipe_kind="SAT_HASH_DIFF",
        source_columns=["ts_utc", "ts_arr", "ts_map"],
        sort_alphabetically=True,
        key_columns=["hk_item"],
    )

    def _make_ir(pid: str, src_id: str, tbl_id: str) -> CertifiedVaultIR:
        return CertifiedVaultIR(
            project_id=pid,
            ontology_version="1.0.0",
            wave_number=1,
            bronze_tables=[BronzeTableSpec(table_name="bronze_tz", source_id=src_id, source_table_id=tbl_id, columns=cols)],
            staging_views=[
                StagingViewSpec(
                    view_name="stg_tz",
                    source_id=src_id,
                    source_table_id=tbl_id,
                    col_lineage_mappings={c.name: c.name for c in cols},
                    hash_recipes=[hk_r, hd_r],
                    projected_fields=cols,
                )
            ],
            hubs=[
                CertifiedHubSpec(
                    hub_name="hub_item",
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    business_keys=[
                        BusinessKeyPart(
                            table_id=tbl_id,
                            col_name="item_id",
                            source_id=src_id,
                            key_tier="TIER_1_GLOBAL_STANDARD",
                            bkcc_value="GLOBAL",
                        )
                    ],
                    source_bindings=[SourceBindingSpec(source_id=src_id, table_id=tbl_id, col_mappings={}, hash_recipe=hk_r)],
                    columns=[
                        IRField(field_id=1, name="hk_item", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                        IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                        IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                        IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                        IRField(field_id=5, name="item_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    ],
                    hash_recipe=hk_r,
                    physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item"]),
                )
            ],
            satellites=[
                CertifiedSatelliteSpec(
                    sat_name="sat_tz",
                    sat_kind="STANDARD",
                    parent_name="hub_item",
                    source_id=src_id,
                    source_table_id=tbl_id,
                    attributes=[
                        DraftField(name="ts_utc", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                        DraftField(name="ts_arr", logical_type=ArrayType(element_type="TIMESTAMP_UTC_US"), nullable=True, is_pii=False),
                        DraftField(name="ts_map", logical_type=MapType(key_type="UTF8_STRING", value_type="TIMESTAMP_UTC_US"), nullable=True, is_pii=False),
                    ],
                    columns=[
                        IRField(field_id=1, name="hk_item", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                        IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                        IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                        IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                        IRField(field_id=5, name="ts_utc", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                        IRField(field_id=6, name="ts_arr", logical_type=ArrayType(element_type="TIMESTAMP_UTC_US"), nullable=True, is_pii=False),
                        IRField(field_id=7, name="ts_map", logical_type=MapType(key_type="UTF8_STRING", value_type="TIMESTAMP_UTC_US"), nullable=True, is_pii=False),
                    ],
                    hash_recipe=hd_r,
                    physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item", "load_dts"]),
                )
            ],
        )

    dt1 = datetime(2026, 10, 20, 18, 0, 0, tzinfo=UTC)
    dt2 = datetime(2026, 10, 21, 9, 30, 0, tzinfo=UTC)

    # 1. Real DuckDB source attachment with TIMESTAMPTZ, TIMESTAMPTZ[], MAP(VARCHAR, TIMESTAMPTZ)
    src_duck = tmp_path / "src_tz.duckdb"
    dconn = duckdb.connect(str(src_duck))
    try:
        dconn.execute(
            "CREATE TABLE items_tz (item_id VARCHAR, ts_utc TIMESTAMPTZ, ts_arr TIMESTAMPTZ[], ts_map MAP(VARCHAR, TIMESTAMPTZ))"
        )
        dconn.execute("INSERT INTO items_tz VALUES (?, ?, ?, ?)", ["A1", dt1, [dt1, dt2], {"k": dt1}])
        dconn.execute("INSERT INTO items_tz VALUES (?, ?, ?, ?)", ["A2", dt2, None, None])
    finally:
        dconn.close()

    ir_d = _make_ir("tz_duck", "dsrc", "dsrc:items_tz")
    d_att = [SourceAttachment(source_id="dsrc", kind="duckdb", path=src_duck)]
    res_d = materialize_duckdb_local(ir_d, tmp_path / "out_duck", d_att, metadata_only=False)
    assert res_d.verified is True and res_d.rows_loaded == 2
    q_d = execute_readonly_query(
        tmp_path / "out_duck/tz_duck.duckdb",
        "SELECT s.ts_utc FROM raw_vault.sat_tz AS s INNER JOIN raw_vault.hub_item AS h "
        "ON h.hk_item = s.hk_item WHERE h.item_id = 'A1'",
    )
    assert q_d.rows == [["2026-10-20T18:00:00.000000Z"]]
    pq_res_d = materialize_parquet_local(ir_d, tmp_path / "out_duck_pq", d_att, metadata_only=False)
    assert pq_res_d.verified is True and pq_res_d.rows_loaded == 2

    # 2. Real Parquet source attachment with UTC, non-UTC tz, list<timestamp[tz]>, map<str, timestamp[tz]>
    src_pq = tmp_path / "items_tz.parquet"
    ptbl = pa.table(
        {
            "item_id": pa.array(["P1", "P2"], type=pa.string()),
            "ts_utc": pa.array([dt1, dt2], type=pa.timestamp("us", tz="America/New_York")),
            "ts_arr": pa.array([[dt1, dt2], None], type=pa.list_(pa.timestamp("us", tz="UTC"))),
            "ts_map": pa.array([[("k", dt1)], None], type=pa.map_(pa.string(), pa.timestamp("us", tz="UTC"))),
        }
    )
    pq.write_table(ptbl, src_pq)

    ir_p = _make_ir("tz_pq", "psrc", "psrc:items_tz")
    p_att = [SourceAttachment(source_id="psrc", kind="parquet", path=src_pq)]
    res_p = materialize_duckdb_local(ir_p, tmp_path / "out_pq", p_att, metadata_only=False)
    assert res_p.verified is True and res_p.rows_loaded == 2
    q_p = execute_readonly_query(
        tmp_path / "out_pq/tz_pq.duckdb",
        "SELECT s.ts_utc FROM raw_vault.sat_tz AS s INNER JOIN raw_vault.hub_item AS h "
        "ON h.hk_item = s.hk_item WHERE h.item_id = 'P1'",
    )
    assert q_p.rows == [["2026-10-20T18:00:00.000000Z"]]
    pq_res_p = materialize_parquet_local(ir_p, tmp_path / "out_pq_pq", p_att, metadata_only=False)
    assert pq_res_p.verified is True and pq_res_p.rows_loaded == 2


def test_iter12_fresh_duckdb_with_prior_ir_uses_ddl_not_migration(tmp_path: Path) -> None:
    """materialize_duckdb_local with prior_ir on a missing db_path executes ddl_sql (not ALTER migration_sql)."""
    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    fresh_dir = tmp_path / "fresh_w2"
    w2_att = [
        SourceAttachment(source_id="am_db", kind="sqlite", path=AM_SQLITE),
        SourceAttachment(source_id="am_esg", kind="parquet", path=AM_PARQUET),
        SourceAttachment(source_id="w2_custody", kind="parquet", path=W2_CUSTODY),
        SourceAttachment(source_id="w2_nav", kind="csv", path=W2_NAV),
        SourceAttachment(source_id="w2_alloc", kind="csv", path=W2_ALLOC),
    ]
    res = materialize_duckdb_local(w2, fresh_dir, w2_att, prior_ir=w1, regenerate=False)
    assert res.verified is True and res.rows_loaded == 650
    db_path = fresh_dir / "am_demo.duckdb"
    assert execute_readonly_query(db_path, "SELECT COUNT(*) FROM raw_vault.hub_custody_acct").rows == [[242]]
    col_type_q = execute_readonly_query(
        db_path,
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = 'raw_vault' AND table_name = 'sat_esg_core' AND column_name = 'voted_shares'",
    )
    assert col_type_q.rows == [["BIGINT"]]


def test_iter14_materialize_duckdb_with_physical_aliases_lock(tmp_path: Path) -> None:
    """materialize_duckdb_local threads bundle.physical_aliases into EVAL-RUN checks."""
    w1 = _build_wave1_am_ir()
    lock_dir = tmp_path / "locked_aliases"
    locks = EvolutionLockSpec(wave_number=1, physical_aliases={"hub_issuer": "hub_issuer_locked"})
    res = materialize_duckdb_local(w1, lock_dir, metadata_only=True, locks=locks)
    assert res.verified is True
    q = execute_readonly_query(lock_dir / "am_demo.duckdb", "SELECT COUNT(*) FROM raw_vault.hub_issuer_locked")
    assert q.rows == [[2]]


def test_iter15_parquet_array_map_real_row_materialize(tmp_path: Path) -> None:
    """Parquet source with non-null ArrayType & MapType passes materialize_duckdb_local EVAL-RUN-01."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from opendv.models.vault_ir import ArrayType, MapType

    cols = [
        IRField(field_id=1, name="item_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="tags_arr", logical_type=ArrayType(element_type="UTF8_STRING"), nullable=True, is_pii=False),
        IRField(field_id=3, name="attrs_map", logical_type=MapType(key_type="UTF8_STRING", value_type="INT64"), nullable=True, is_pii=False),
    ]
    hk_r = HashRecipeSpec(
        target_col="hk_item", recipe_kind="HUB_HK", bkcc_literal="GLOBAL",
        source_columns=["item_id"], sort_alphabetically=False, key_columns=["item_id"],
    )
    hd_r = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=["tags_arr", "attrs_map"], sort_alphabetically=True, key_columns=["hk_item"],
    )
    ir = CertifiedVaultIR(
        project_id="pq_coll",
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[BronzeTableSpec(table_name="bronze_coll", source_id="ps", source_table_id="ps:items_coll", columns=cols)],
        staging_views=[
            StagingViewSpec(
                view_name="stg_coll", source_id="ps", source_table_id="ps:items_coll",
                col_lineage_mappings={c.name: c.name for c in cols}, hash_recipes=[hk_r, hd_r], projected_fields=cols,
            )
        ],
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_item", key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[BusinessKeyPart(table_id="ps:items_coll", col_name="item_id", source_id="ps", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
                source_bindings=[SourceBindingSpec(source_id="ps", table_id="ps:items_coll", col_mappings={}, hash_recipe=hk_r)],
                columns=[
                    IRField(field_id=1, name="hk_item", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="item_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=hk_r, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item"]),
            )
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name="sat_coll", sat_kind="STANDARD", parent_name="hub_item",
                source_id="ps", source_table_id="ps:items_coll",
                attributes=[
                    DraftField(name="tags_arr", logical_type=ArrayType(element_type="UTF8_STRING"), nullable=True, is_pii=False),
                    DraftField(name="attrs_map", logical_type=MapType(key_type="UTF8_STRING", value_type="INT64"), nullable=True, is_pii=False),
                ],
                columns=[
                    IRField(field_id=1, name="hk_item", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="tags_arr", logical_type=ArrayType(element_type="UTF8_STRING"), nullable=True, is_pii=False),
                    IRField(field_id=6, name="attrs_map", logical_type=MapType(key_type="UTF8_STRING", value_type="INT64"), nullable=True, is_pii=False),
                ],
                hash_recipe=hd_r, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item", "load_dts"]),
            )
        ],
    )
    pq_path = tmp_path / "items_coll.parquet"
    pq.write_table(
        pa.table(
            {
                "item_id": pa.array(["I1", "I2"], type=pa.string()),
                "tags_arr": pa.array([["t1", "äöü"], None], type=pa.list_(pa.string())),
                "attrs_map": pa.array([[("b", 2), ("a", 1)], []], type=pa.map_(pa.string(), pa.int64())),
            }
        ),
        pq_path,
    )
    res = materialize_duckdb_local(
        ir, tmp_path / "out_pq_coll", [SourceAttachment(source_id="ps", kind="parquet", path=pq_path)], metadata_only=False
    )
    assert res.verified is True and res.rows_loaded == 2


def test_iter17_parquet_emitter_recursive_collection_coercion(tmp_path: Path) -> None:
    """ParquetEmitter._coerce_arrow_value recurses into ArrayType and MapType for uncoerced nested primitives."""
    from datetime import UTC, date, datetime
    from decimal import Decimal

    import pyarrow.parquet as pq

    from opendv.models.vault_ir import ArrayType, MapType
    from opendv.storage import ParquetEmitter
    from opendv.storage.emitters.parquet import _coerce_arrow_value

    arr_dec_t = ArrayType(element_type=DecimalType(precision=38, scale=10))
    arr_date_t = ArrayType(element_type="DATE32")
    map_ts_arr_dec_t = MapType(key_type="TIMESTAMP_UTC_US", value_type=arr_dec_t)

    assert _coerce_arrow_value([0.00001, "100.25", None], arr_dec_t) == [
        Decimal("0.00001"),
        Decimal("100.25"),
        None,
    ]
    assert _coerce_arrow_value(["2026-01-01", date(2026, 2, 2)], arr_date_t) == [
        date(2026, 1, 1),
        date(2026, 2, 2),
    ]
    assert _coerce_arrow_value({"2026-10-20T18:00:00Z": [0.00001, None]}, map_ts_arr_dec_t) == [
        (datetime(2026, 10, 20, 18, 0, tzinfo=UTC), [Decimal("0.00001"), None])
    ]
    assert _coerce_arrow_value("2026-10-20T18:00:00z", "TIMESTAMP_UTC_US") == datetime(
        2026, 10, 20, 18, 0, tzinfo=UTC
    )

    cols = [
        IRField(field_id=1, name="item_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="dec_arr", logical_type=arr_dec_t, nullable=True, is_pii=False),
        IRField(field_id=3, name="dt_arr", logical_type=arr_date_t, nullable=True, is_pii=False),
        IRField(field_id=4, name="ts_map", logical_type=map_ts_arr_dec_t, nullable=True, is_pii=False),
    ]
    hk_r = HashRecipeSpec(
        target_col="hk_item", recipe_kind="HUB_HK", bkcc_literal="GLOBAL",
        source_columns=["item_id"], sort_alphabetically=False, key_columns=["item_id"],
    )
    hd_r = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=["dec_arr", "dt_arr", "ts_map"], sort_alphabetically=True, key_columns=["hk_item"],
    )
    ir = CertifiedVaultIR(
        project_id="pq_rec",
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[BronzeTableSpec(table_name="bronze_rec", source_id="s", source_table_id="s:rec", columns=cols)],
        staging_views=[
            StagingViewSpec(
                view_name="stg_rec", source_id="s", source_table_id="s:rec",
                col_lineage_mappings={c.name: c.name for c in cols}, hash_recipes=[hk_r, hd_r], projected_fields=cols,
            )
        ],
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_item", key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[BusinessKeyPart(table_id="s:rec", col_name="item_id", source_id="s", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
                source_bindings=[SourceBindingSpec(source_id="s", table_id="s:rec", col_mappings={}, hash_recipe=hk_r)],
                columns=[
                    IRField(field_id=1, name="hk_item", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="item_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=hk_r, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item"]),
            )
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name="sat_rec", sat_kind="STANDARD", parent_name="hub_item",
                source_id="s", source_table_id="s:rec",
                attributes=[
                    DraftField(name="dec_arr", logical_type=arr_dec_t, nullable=True, is_pii=False),
                    DraftField(name="dt_arr", logical_type=arr_date_t, nullable=True, is_pii=False),
                    DraftField(name="ts_map", logical_type=map_ts_arr_dec_t, nullable=True, is_pii=False),
                ],
                columns=[
                    IRField(field_id=1, name="hk_item", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="dec_arr", logical_type=arr_dec_t, nullable=True, is_pii=False),
                    IRField(field_id=6, name="dt_arr", logical_type=arr_date_t, nullable=True, is_pii=False),
                    IRField(field_id=7, name="ts_map", logical_type=map_ts_arr_dec_t, nullable=True, is_pii=False),
                ],
                hash_recipe=hd_r, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item", "load_dts"]),
            )
        ],
    )
    out_dir = tmp_path / "pq_emit_rec"
    ParquetEmitter().emit(
        ir,
        out_dir,
        rows_by_entity={
            "bronze_rec": [
                {
                    "item_id": "R1",
                    "dec_arr": [0.00001, 100.25],
                    "dt_arr": ["2026-01-01"],
                    "ts_map": {"2026-10-20T18:00:00Z": [0.00001]},
                }
            ]
        },
    )
    from opendv.storage._eval_runtime import arrow_table_to_pylist_utc

    tbl = pq.read_table(out_dir / "bronze/bronze_rec/wave=W001/part-00000.zstd.parquet")
    rows = arrow_table_to_pylist_utc(tbl)
    assert len(rows) == 1
    assert rows[0]["dec_arr"] == [Decimal("0.0000100000"), Decimal("100.2500000000")]
    assert rows[0]["dt_arr"] == [date(2026, 1, 1)]
    assert rows[0]["ts_map"] == {
        datetime(2026, 10, 20, 18, 0, tzinfo=UTC): [Decimal("0.0000100000")]
    }


def test_iter46_sat_dml_deterministic_tiebreaker_reversed_row_order(tmp_path: Path) -> None:
    """Verify _sat_where_and_dedup emits deterministic tie-breakers and yields identical nav_hist on reversed CSV rows."""
    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    verifier = SqlAstVerifier()

    for tid in SQL_TARGET_IDS:
        bundle = SqlGlotVaultCompiler(tid).compile(w2)
        assert verifier.verify_sql(bundle.full_sql, tid, ir=w2) == [], tid
        assert "src.hash_diff DESC, src.rec_src ASC" in bundle.full_sql, tid

    lines = W2_NAV.read_text(encoding="utf-8").splitlines()
    rev_nav = tmp_path / W2_NAV.name
    rev_nav.write_text("\n".join([lines[0], *reversed(lines[1:])]) + "\n", encoding="utf-8")

    out = tmp_path / "targets/duckdb_local_rev"
    w1_att = [
        SourceAttachment(source_id="am_db", kind="sqlite", path=AM_SQLITE),
        SourceAttachment(source_id="am_esg", kind="parquet", path=AM_PARQUET),
    ]
    r1 = materialize_duckdb_local(w1, out, w1_att)
    assert r1.verified is True and r1.rows_loaded == 80
    w2_att = [
        *w1_att,
        SourceAttachment(source_id="w2_custody", kind="parquet", path=W2_CUSTODY),
        SourceAttachment(source_id="w2_nav", kind="csv", path=rev_nav),
        SourceAttachment(source_id="w2_alloc", kind="csv", path=W2_ALLOC),
    ]
    r2 = materialize_duckdb_local(w2, out, w2_att, prior_ir=w1)
    assert r2.verified is True and r2.rows_loaded == 570
    nav_hist = execute_readonly_query(
        out / "am_demo.duckdb",
        "SELECT s.applied_dts, s.nav_per_share FROM raw_vault.hub_fund_nav AS h "
        "INNER JOIN raw_vault.sat_nav_bitemp AS s ON s.hk_fund_nav = h.hk_fund_nav "
        "WHERE h.fund_code = 'AM-CORE-EQ' AND h.share_class = 'INST_A' AND h.as_of_date = DATE '2026-10-20' "
        "ORDER BY s.applied_dts ASC",
    )
    assert [r[1] for r in nav_hist.rows] == [216.28, 245.0, 156.25]
