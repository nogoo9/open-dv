"""Phase V2-4 tests: DuckDbProverAdapter (V6), 15/15 DV-DYN, Alg A.6, DV-AP-011, ds_cegar_12trap."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from opendv.models.diagnostics import KeyTierEnum
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
from opendv.models.vault_ir import (
    BusinessKeyPart,
    ColumnTriageEntry,
    DecimalType,
    DraftField,
    LinkParticipantSpec,
    PredicateLeaf,
)
from opendv.models.vault_ir_certified import (
    BronzeTableSpec,
    CertifiedHubSpec,
    CertifiedLinkSpec,
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    HashRecipeSpec,
    IRField,
    LoadSemanticsSpec,
    MartViewSpec,
    PhysicalLayoutHints,
    PitBridgeSpec,
    SourceBindingSpec,
    StagingViewSpec,
)
from opendv.storage.prover import (
    DuckDbProverAdapter,
    decollide_masked_pii,
    run_adversarial_batches,
    run_all_dyn_invariants,
)
from opendv.storage.prover.edge_batches import AdversarialBatchSummary
from opendv.storage.sql_ast_verifier import SqlAstVerifier
from opendv.storage.sql_compiler import SqlGlotVaultCompiler
from opendv.verifier.ports import IRRuntimeProver, VerifierContext
from opendv.verifier.v2_5_gates import run_v1_to_v6_gates

REPO_ROOT = Path(__file__).resolve().parents[2]
TRAP_DUCKDB = REPO_ROOT / "data/public_am/ds_cegar_12trap.duckdb"


def _load_trap_profiles_and_rows() -> tuple[list[SourceProfile], dict[str, list[dict[str, object]]]]:
    """Attach real data/public_am/ds_cegar_12trap.duckdb read-only and extract profiles + rows."""
    assert TRAP_DUCKDB.exists(), f"Missing fixture {TRAP_DUCKDB}"
    conn = duckdb.connect(str(TRAP_DUCKDB), read_only=True)
    try:
        tables: list[TableProfile] = []
        rows_map: dict[str, list[dict[str, object]]] = {}
        t_names = [
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY table_name"
            ).fetchall()
        ]
        for tname in t_names:
            cur = conn.execute(f"SELECT * FROM {tname}")
            col_names = [d[0] for d in (cur.description or ())]
            raw_rows = cur.fetchall()
            cols_meta = conn.execute(f"PRAGMA table_info('{tname}')").fetchall()
            tid = f"trap_src:{tname}"
            rows_map[tid] = [dict(zip(col_names, r, strict=True)) for r in raw_rows]
            col_profiles: list[ColumnProfile] = []
            for idx, meta in enumerate(cols_meta):
                _cid, cname, ctype, notnull, _, is_pk = meta
                samples = [
                    None if r[idx] is None else str(r[idx]) for r in raw_rows[:5]
                ]
                col_profiles.append(
                    ColumnProfile(
                        col_name=str(cname),
                        sql_type=str(ctype),
                        nullable=not bool(notnull),
                        is_pk=bool(is_pk),
                        null_ratio=0.0,
                        distinct_count_est=len({r[idx] for r in raw_rows}),
                        pii_tags=["EMAIL"] if "email" in str(cname) or "tax_id" in str(cname) or "phone" in str(cname) else [],
                        sample_values=samples,
                    )
                )
            tables.append(
                TableProfile(table_id=tid, table_name=tname, row_count_est=len(raw_rows), columns=col_profiles)
            )
        return [
            SourceProfile(
                source_id="trap_src",
                schema_fingerprint="trap12",
                table_count=len(tables),
                tables=tables,
            )
        ], rows_map
    finally:
        conn.close()


def _build_clean_trap_ir(
    *,
    include_tech_churn_in_hd: bool = False,
    unpromote_ballot_mas_to_sat: bool = False,
    profiles: Sequence[SourceProfile] = (),
) -> CertifiedVaultIR:
    """Build a generalized CertifiedVaultIR covering the 6 tables in ds_cegar_12trap.duckdb."""
    # 1. Bronze tables with load_dts/rec_src omitted -> Z1 generates deterministic CURRENT_TIMESTAMP + 'trap_src'
    b_crm_cols = [
        IRField(field_id=1, name="account_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="delimiter_part_a", logical_type="UTF8_STRING", nullable=True, is_pii=False),
        IRField(field_id=3, name="delimiter_part_b", logical_type="UTF8_STRING", nullable=True, is_pii=False),
        IRField(field_id=4, name="beneficial_owner_tax_id", logical_type="UTF8_STRING", nullable=True, is_pii=True),
        IRField(field_id=5, name="daily_nav_usd", logical_type=DecimalType(precision=18, scale=2), nullable=True, is_pii=False),
    ]
    b_ibor_cols = [
        IRField(field_id=1, name="account_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="issuer_cik", logical_type="UTF8_STRING", nullable=True, is_pii=False),
        IRField(field_id=3, name="issuer_legal_name", logical_type="UTF8_STRING", nullable=True, is_pii=False),
    ]
    b_bal_cols = [
        IRField(field_id=1, name="ballot_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="proposal_seq", logical_type="INT32", nullable=False, is_pii=False),
        IRField(field_id=3, name="vote_cast_code", logical_type="UTF8_STRING", nullable=True, is_pii=False),
    ]
    b_snap_cols = [
        IRField(field_id=1, name="position_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="quantity", logical_type=DecimalType(precision=18, scale=4), nullable=True, is_pii=False),
        IRField(field_id=3, name="etl_snapshot_ts", logical_type="TIMESTAMP_UTC_US", nullable=True, is_pii=False),
    ]
    b_eff_cols = [
        IRField(field_id=1, name="assignment_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="account_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=3, name="portfolio_manager_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=4, name="effective_from", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
    ]
    b_trd_cols = [
        IRField(field_id=1, name="trade_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="as_of_date", logical_type="DATE32", nullable=False, is_pii=False),
        IRField(field_id=3, name="applied_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
        IRField(field_id=4, name="nav_per_share", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
    ]

    r_crm = HashRecipeSpec(
        target_col="hk_crm_acct", recipe_kind="HUB_HK", bkcc_literal="CRM",
        source_columns=["account_id"], sort_alphabetically=False, key_columns=["account_id"],
    )
    r_ibor = HashRecipeSpec(
        target_col="hk_ibor_acct", recipe_kind="HUB_HK", bkcc_literal="IBOR",
        source_columns=["account_id"], sort_alphabetically=False, key_columns=["account_id"],
    )
    r_bal = HashRecipeSpec(
        target_col="hk_ballot", recipe_kind="HUB_HK", bkcc_literal="GLOBAL",
        source_columns=["ballot_id"], sort_alphabetically=False, key_columns=["ballot_id"],
    )
    r_pos = HashRecipeSpec(
        target_col="hk_position", recipe_kind="HUB_HK", bkcc_literal="GLOBAL",
        source_columns=["position_id"], sort_alphabetically=False, key_columns=["position_id"],
    )
    r_mgr = HashRecipeSpec(
        target_col="hk_manager", recipe_kind="HUB_HK", bkcc_literal="GLOBAL",
        source_columns=["portfolio_manager_id"], sort_alphabetically=False, key_columns=["portfolio_manager_id"],
    )
    r_trd = HashRecipeSpec(
        target_col="hk_trade", recipe_kind="HUB_HK", bkcc_literal="GLOBAL",
        source_columns=["trade_id"], sort_alphabetically=False, key_columns=["trade_id"],
    )
    r_lnk_eff = HashRecipeSpec(
        target_col="hk_acct_mgr", recipe_kind="LINK_HK", bkcc_literal="GLOBAL",
        source_columns=["account_id", "portfolio_manager_id"], sort_alphabetically=False,
        key_columns=["account_id", "portfolio_manager_id"],
    )

    hd_crm = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=["daily_nav_usd", "delimiter_part_a", "delimiter_part_b"],
        sort_alphabetically=True, key_columns=["hk_crm_acct"],
    )
    hd_pii = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=["beneficial_owner_tax_id"], sort_alphabetically=True, key_columns=["hk_crm_acct"],
    )
    hd_ibor = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=["issuer_cik", "issuer_legal_name"], sort_alphabetically=True, key_columns=["hk_ibor_acct"],
    )
    hd_bal = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=["vote_cast_code"] if unpromote_ballot_mas_to_sat else ["proposal_seq", "vote_cast_code"],
        sort_alphabetically=True, key_columns=["hk_ballot"],
    )
    snap_src_cols = ["etl_snapshot_ts", "quantity"] if include_tech_churn_in_hd else ["quantity"]
    hd_snap = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=snap_src_cols, sort_alphabetically=True, key_columns=["hk_position"],
    )
    hd_trd = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=["as_of_date", "nav_per_share"], sort_alphabetically=True, key_columns=["hk_trade"],
    )

    triage = [
        ColumnTriageEntry(
            table_id=tbl.table_id,
            col_name=col.col_name,
            disposition="SYSTEM_METADATA" if col.col_name == "etl_snapshot_ts" else "MODELED",
            target_ref="hub_crm_acct",
        )
        for p in profiles
        for tbl in p.tables
        for col in tbl.columns
    ]

    return CertifiedVaultIR(
        project_id="proj_12trap",
        ontology_version="1.0.0",
        wave_number=1,
        column_triage=triage,
        bronze_tables=[
            BronzeTableSpec(table_name="bronze_crm", source_id="trap_src", source_table_id="trap_src:trap_crm_accounts", columns=b_crm_cols),
            BronzeTableSpec(table_name="bronze_ibor", source_id="trap_src", source_table_id="trap_src:trap_ibor_accounts", columns=b_ibor_cols),
            BronzeTableSpec(table_name="bronze_ballots", source_id="trap_src", source_table_id="trap_src:trap_ballots_mas", columns=b_bal_cols),
            BronzeTableSpec(table_name="bronze_snap", source_id="trap_src", source_table_id="trap_src:trap_snapshots_churn", columns=b_snap_cols),
            BronzeTableSpec(table_name="bronze_eff", source_id="trap_src", source_table_id="trap_src:trap_manager_eff", columns=b_eff_cols),
            BronzeTableSpec(table_name="bronze_trd", source_id="trap_src", source_table_id="trap_src:trap_early_trades", columns=b_trd_cols),
        ],
        staging_views=[
            StagingViewSpec(view_name="stg_crm", source_id="trap_src", source_table_id="trap_src:trap_crm_accounts", col_lineage_mappings={c.name: c.name for c in b_crm_cols}, hash_recipes=[r_crm, hd_crm, hd_pii], projected_fields=b_crm_cols),
            StagingViewSpec(view_name="stg_ibor", source_id="trap_src", source_table_id="trap_src:trap_ibor_accounts", col_lineage_mappings={c.name: c.name for c in b_ibor_cols}, hash_recipes=[r_ibor, hd_ibor], projected_fields=b_ibor_cols),
            StagingViewSpec(view_name="stg_ballots", source_id="trap_src", source_table_id="trap_src:trap_ballots_mas", col_lineage_mappings={c.name: c.name for c in b_bal_cols}, hash_recipes=[r_bal, hd_bal], projected_fields=b_bal_cols),
            StagingViewSpec(view_name="stg_snap", source_id="trap_src", source_table_id="trap_src:trap_snapshots_churn", col_lineage_mappings={c.name: c.name for c in b_snap_cols}, hash_recipes=[r_pos, hd_snap], projected_fields=b_snap_cols),
            StagingViewSpec(view_name="stg_eff", source_id="trap_src", source_table_id="trap_src:trap_manager_eff", col_lineage_mappings={**{c.name: c.name for c in b_eff_cols}, "load_dts": "effective_from"}, hash_recipes=[r_crm, r_mgr, r_lnk_eff], projected_fields=b_eff_cols),
            StagingViewSpec(view_name="stg_trd", source_id="trap_src", source_table_id="trap_src:trap_early_trades", col_lineage_mappings={c.name: c.name for c in b_trd_cols}, hash_recipes=[r_trd, hd_trd], projected_fields=b_trd_cols),
        ],
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_crm_acct", key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[BusinessKeyPart(table_id="trap_src:trap_crm_accounts", col_name="account_id", source_id="trap_src", key_tier="TIER_3_LOCAL_SCOPED", bkcc_value="CRM")],
                source_bindings=[
                    SourceBindingSpec(source_id="trap_src", table_id="trap_src:trap_crm_accounts", col_mappings={}, hash_recipe=r_crm),
                    SourceBindingSpec(source_id="trap_src", table_id="trap_src:trap_manager_eff", col_mappings={}, hash_recipe=r_crm),
                ],
                columns=[
                    IRField(field_id=1, name="hk_crm_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="account_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_crm, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_crm_acct"]),
            ),
            CertifiedHubSpec(
                hub_name="hub_ibor_acct", key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[BusinessKeyPart(table_id="trap_src:trap_ibor_accounts", col_name="account_id", source_id="trap_src", key_tier="TIER_3_LOCAL_SCOPED", bkcc_value="IBOR")],
                source_bindings=[SourceBindingSpec(source_id="trap_src", table_id="trap_src:trap_ibor_accounts", col_mappings={}, hash_recipe=r_ibor)],
                columns=[
                    IRField(field_id=1, name="hk_ibor_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="account_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_ibor, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_ibor_acct"]),
            ),
            CertifiedHubSpec(
                hub_name="hub_ballot", key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[BusinessKeyPart(table_id="trap_src:trap_ballots_mas", col_name="ballot_id", source_id="trap_src", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
                source_bindings=[SourceBindingSpec(source_id="trap_src", table_id="trap_src:trap_ballots_mas", col_mappings={}, hash_recipe=r_bal)],
                columns=[
                    IRField(field_id=1, name="hk_ballot", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="ballot_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_bal, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_ballot"]),
            ),
            CertifiedHubSpec(
                hub_name="hub_position", key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[BusinessKeyPart(table_id="trap_src:trap_snapshots_churn", col_name="position_id", source_id="trap_src", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
                source_bindings=[SourceBindingSpec(source_id="trap_src", table_id="trap_src:trap_snapshots_churn", col_mappings={}, hash_recipe=r_pos)],
                columns=[
                    IRField(field_id=1, name="hk_position", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="position_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_pos, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_position"]),
            ),
            CertifiedHubSpec(
                hub_name="hub_manager", key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[BusinessKeyPart(table_id="trap_src:trap_manager_eff", col_name="portfolio_manager_id", source_id="trap_src", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
                source_bindings=[SourceBindingSpec(source_id="trap_src", table_id="trap_src:trap_manager_eff", col_mappings={}, hash_recipe=r_mgr)],
                columns=[
                    IRField(field_id=1, name="hk_manager", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="portfolio_manager_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_mgr, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_manager"]),
            ),
            CertifiedHubSpec(
                hub_name="hub_trade", key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[BusinessKeyPart(table_id="trap_src:trap_early_trades", col_name="trade_id", source_id="trap_src", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
                source_bindings=[SourceBindingSpec(source_id="trap_src", table_id="trap_src:trap_early_trades", col_mappings={}, hash_recipe=r_trd)],
                columns=[
                    IRField(field_id=1, name="hk_trade", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="trade_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_trd, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_trade"]),
            ),
        ],
        links=[
            CertifiedLinkSpec(
                link_name="lnk_acct_mgr", link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(hub_name="hub_crm_acct", role_name="ACCOUNT", fk_col_name="hk_crm_acct", source_col_names=["account_id"]),
                    LinkParticipantSpec(hub_name="hub_manager", role_name="MANAGER", fk_col_name="hk_manager", source_col_names=["portfolio_manager_id"]),
                ],
                source_bindings=[SourceBindingSpec(source_id="trap_src", table_id="trap_src:trap_manager_eff", col_mappings={}, hash_recipe=r_lnk_eff)],
                columns=[
                    IRField(field_id=1, name="hk_acct_mgr", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="hk_crm_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=3, name="hk_manager", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=4, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=5, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_lnk_eff, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_acct_mgr"]),
            )
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name="sat_crm_core", sat_kind="STANDARD", parent_name="hub_crm_acct",
                source_id="trap_src", source_table_id="trap_src:trap_crm_accounts",
                attributes=[
                    DraftField(name="delimiter_part_a", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                    DraftField(name="delimiter_part_b", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                    DraftField(name="daily_nav_usd", logical_type=DecimalType(precision=18, scale=2), nullable=True, is_pii=False),
                ],
                columns=[
                    IRField(field_id=1, name="hk_crm_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="delimiter_part_a", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                    IRField(field_id=6, name="delimiter_part_b", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                    IRField(field_id=7, name="daily_nav_usd", logical_type=DecimalType(precision=18, scale=2), nullable=True, is_pii=False),
                ],
                hash_recipe=hd_crm, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_crm_acct", "load_dts"]),
            ),
            CertifiedSatelliteSpec(
                sat_name="sat_crm_pii", sat_kind="PII", parent_name="hub_crm_acct",
                source_id="trap_src", source_table_id="trap_src:trap_crm_accounts",
                attributes=[DraftField(name="beneficial_owner_tax_id", logical_type="UTF8_STRING", nullable=True, is_pii=True)],
                columns=[
                    IRField(field_id=1, name="hk_crm_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="beneficial_owner_tax_id", logical_type="UTF8_STRING", nullable=True, is_pii=True),
                ],
                hash_recipe=hd_pii, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_crm_acct", "load_dts"]),
            ),
            CertifiedSatelliteSpec(
                sat_name="sat_ibor_core", sat_kind="STANDARD", parent_name="hub_ibor_acct",
                source_id="trap_src", source_table_id="trap_src:trap_ibor_accounts",
                attributes=[
                    DraftField(name="issuer_cik", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                    DraftField(name="issuer_legal_name", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                ],
                columns=[
                    IRField(field_id=1, name="hk_ibor_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="issuer_cik", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                    IRField(field_id=6, name="issuer_legal_name", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                ],
                hash_recipe=hd_ibor, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_ibor_acct", "load_dts"]),
            ),
            CertifiedSatelliteSpec(
                sat_name="sat_ballot_core" if unpromote_ballot_mas_to_sat else "mas_ballots",
                sat_kind="STANDARD" if unpromote_ballot_mas_to_sat else "MAS",
                parent_name="hub_ballot",
                source_id="trap_src", source_table_id="trap_src:trap_ballots_mas",
                child_dependent_keys=[] if unpromote_ballot_mas_to_sat else ["proposal_seq"],
                attributes=[DraftField(name="vote_cast_code", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
                columns=(
                    [
                        IRField(field_id=1, name="hk_ballot", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                        IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                        IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                        IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                        IRField(field_id=5, name="vote_cast_code", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                    ]
                    if unpromote_ballot_mas_to_sat
                    else [
                        IRField(field_id=1, name="hk_ballot", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                        IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                        IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                        IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                        IRField(field_id=5, name="proposal_seq", logical_type="INT32", nullable=False, is_pii=False),
                        IRField(field_id=6, name="vote_cast_code", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                    ]
                ),
                hash_recipe=hd_bal, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_ballot", "load_dts"]),
            ),
            CertifiedSatelliteSpec(
                sat_name="sat_snap", sat_kind="STANDARD", parent_name="hub_position",
                source_id="trap_src", source_table_id="trap_src:trap_snapshots_churn",
                attributes=[
                    DraftField(name="quantity", logical_type=DecimalType(precision=18, scale=4), nullable=True, is_pii=False),
                    *(
                        [DraftField(name="etl_snapshot_ts", logical_type="TIMESTAMP_UTC_US", nullable=True, is_pii=False)]
                        if include_tech_churn_in_hd
                        else []
                    ),
                ],
                columns=[
                    IRField(field_id=1, name="hk_position", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="quantity", logical_type=DecimalType(precision=18, scale=4), nullable=True, is_pii=False),
                    *(
                        [IRField(field_id=6, name="etl_snapshot_ts", logical_type="TIMESTAMP_UTC_US", nullable=True, is_pii=False)]
                        if include_tech_churn_in_hd
                        else []
                    ),
                ],
                hash_recipe=hd_snap, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_position", "load_dts"]),
            ),
            CertifiedSatelliteSpec(
                sat_name="esat_acct_mgr", sat_kind="ESAT", parent_name="lnk_acct_mgr",
                source_id="trap_src", source_table_id="trap_src:trap_manager_eff",
                driving_keys=["hk_crm_acct"],
                attributes=[DraftField(name="effective_from", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False)],
                columns=[
                    IRField(field_id=1, name="hk_acct_mgr", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hk_crm_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="effective_from", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                ],
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_acct_mgr", "load_dts"]),
            ),
            CertifiedSatelliteSpec(
                sat_name="sat_trade_bitemp", sat_kind="STANDARD", parent_name="hub_trade",
                source_id="trap_src", source_table_id="trap_src:trap_early_trades",
                is_bitemporal=True, applied_dts_col="applied_dts",
                attributes=[
                    DraftField(name="as_of_date", logical_type="DATE32", nullable=False, is_pii=False),
                    DraftField(name="nav_per_share", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
                ],
                columns=[
                    IRField(field_id=1, name="hk_trade", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="applied_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=6, name="as_of_date", logical_type="DATE32", nullable=False, is_pii=False),
                    IRField(field_id=7, name="nav_per_share", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
                ],
                hash_recipe=hd_trd, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_trade", "load_dts"]),
            ),
        ],
        pit_bridges=[
            PitBridgeSpec(
                object_name="pit_crm_acct", object_kind="PIT", root_hub="hub_crm_acct",
                target_satellites_or_links=["sat_crm_core", "sat_crm_pii"],
                grain_cols=["hk_crm_acct", "snapshot_dts"],
            ),
            PitBridgeSpec(
                object_name="pit_trade", object_kind="PIT", root_hub="hub_trade",
                target_satellites_or_links=["sat_trade_bitemp"],
                grain_cols=["hk_trade", "snapshot_dts"],
            ),
        ],
        information_marts=[
            MartViewSpec(
                view_name="information_mart.vw_crm_acct_current",
                root_entity="hub_crm_acct",
                joined_entities=["sat_crm_core", "sat_crm_pii"],
                columns=[
                    IRField(field_id=1, name="hk_crm_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="account_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="daily_nav_usd", logical_type=DecimalType(precision=18, scale=2), nullable=True, is_pii=False),
                ],
                select_exprs={
                    "hk_crm_acct": "hub_crm_acct.hk_crm_acct",
                    "account_id": "hub_crm_acct.account_id",
                    "daily_nav_usd": "sat_crm_core.daily_nav_usd",
                },
            )
        ],
    )


def test_duckdb_prover_implements_port_and_passes_15_dv_dyn_on_ds_cegar_12trap() -> None:
    """Real ds_cegar_12trap.duckdb passes 15/15 DV-DYN invariants, 0 ast_violations, 0 smells when clean."""
    prover = DuckDbProverAdapter()
    assert isinstance(prover, IRRuntimeProver)

    profiles, rows_map = _load_trap_profiles_and_rows()
    ir = _build_clean_trap_ir(include_tech_churn_in_hd=False, unpromote_ballot_mas_to_sat=False)
    report = prover.prove(ir, profiles, source_rows_by_table=rows_map)

    assert len(report.dyn_results) == 15
    assert all(report.dyn_results.values()), {k: v for k, v in report.dyn_results.items() if not v}
    assert report.ast_violations == []
    assert report.empirical_smells == []

    # Real run_v1_to_v6_gates with profiles alone (no source_rows_by_table)
    v_rep = run_v1_to_v6_gates(
        _build_clean_trap_ir(profiles=profiles),
        VerifierContext(profiles=profiles, policy="balanced"),
        prover=prover,
        include_v6b=True,
    )
    assert v_rep.passed is True, v_rep.v1_v6_diagnostics
    assert v_rep.v1_v6_fail_count == 0
    assert v_rep.prover_report is not None
    assert all(v_rep.prover_report.dyn_results.values())

    # Un-promoted MAS fails DV-DYN-004 through run_v1_to_v6_gates with profiles alone
    v_rep_bad = run_v1_to_v6_gates(
        _build_clean_trap_ir(unpromote_ballot_mas_to_sat=True, profiles=profiles),
        VerifierContext(profiles=profiles, policy="balanced"),
        prover=prover,
        include_v6b=True,
    )
    assert v_rep_bad.passed is False
    assert v_rep_bad.prover_report is not None
    assert v_rep_bad.prover_report.dyn_results["DV-DYN-004"] is False

    # Direct Batches 1..4 verification: 3-row A->B->A boomerang + Batch 4 +31d restatement & PIT rebuild
    bundle = SqlGlotVaultCompiler("duckdb_local").compile(ir)
    conn = duckdb.connect(":memory:")
    try:
        summary = run_adversarial_batches(conn, ir, profiles, bundle, source_rows_by_table=rows_map)
        assert summary.boundary_shift_ok is True
        assert summary.bitemporal_proof_ok is True
        esat_cnt = conn.execute("SELECT COUNT(*) FROM raw_vault.esat_acct_mgr").fetchone()
        assert esat_cnt and int(esat_cnt[0]) == 4  # 3 real PM-A->PM-B->PM-A transitions + 1 Ghost
        b4_bronze_cnt = conn.execute("SELECT COUNT(*) FROM bronze.bronze_trd").fetchone()
        b4_sat_cnt = conn.execute("SELECT COUNT(*) FROM raw_vault.sat_trade_bitemp").fetchone()
        b4_pit_cnt = conn.execute("SELECT COUNT(*) FROM business_vault.pit_trade").fetchone()
        assert b4_bronze_cnt and int(b4_bronze_cnt[0]) == 3  # 2 Batch-1 + 1 Batch-4 restatement
        assert b4_sat_cnt and int(b4_sat_cnt[0]) == 4  # 1 Ghost + 2 Batch-1 + 1 Batch-4
        assert b4_pit_cnt and int(b4_pit_cnt[0]) == 5  # rebuilt PIT includes +31d snapshot
    finally:
        conn.close()


def test_duckdb_prover_sandbox_isolation_and_pii_decollision() -> None:
    """Verify enable_external_access=false + lock_configuration=true and <MASKED_PII_#i> de-collision."""
    # 1. External access locked in DuckDbProverAdapter config
    conn = duckdb.connect(
        ":memory:",
        config={"memory_limit": "256MB", "threads": 2, "enable_external_access": False, "lock_configuration": True},
    )
    with pytest.raises(duckdb.PermissionException):
        conn.execute("SELECT * FROM read_csv('nonexistent.csv')")
    with pytest.raises(duckdb.InvalidInputException):
        conn.execute("SET enable_external_access = true")
    conn.close()

    # 2. <MASKED_PII> de-collision prevents synthetic BK collision
    decollided, next_idx = decollide_masked_pii(
        [{"tax_id": "<MASKED_PII>"}, {"tax_id": "<MASKED_PII>"}, {"tax_id": "REAL_VAL"}]
    )
    assert [r["tax_id"] for r in decollided] == ["<MASKED_PII_#1>", "<MASKED_PII_#2>", "REAL_VAL"]
    assert next_idx == 3

    # 3. Masked PII profile rows pass DV-DYN-001 through DuckDbProverAdapter
    ir = _build_clean_trap_ir()
    profiles, _ = _load_trap_profiles_and_rows()
    for p in profiles:
        for t in p.tables:
            if t.table_name == "trap_crm_accounts":
                t.columns[0].sample_values = ["<MASKED_PII>", "<MASKED_PII>", "<MASKED_PII>"]
    rep = DuckDbProverAdapter().prove(ir, profiles)
    assert rep.dyn_results["DV-DYN-001"] is True


def test_batch3_dv_ap_011_churn_and_trap04_unpromoted_sat_gate_wiring() -> None:
    """Verify Batch-3 DV-AP-011 (>95% churn) -> |V6b_fail| and TRAP-04 un-promoted sat -> DV-DYN-004."""
    profiles, rows_map = _load_trap_profiles_and_rows()

    # 1. Technical column etl_snapshot_ts inside hash_diff -> DV-AP-011 (>95% churn)
    ir_churn = _build_clean_trap_ir(include_tech_churn_in_hd=True)
    rep_churn = DuckDbProverAdapter().prove(ir_churn, profiles, source_rows_by_table=rows_map)
    assert any(d.rule_id == "DV-AP-011" and d.waivable and d.severity == "WARN" for d in rep_churn.empirical_smells)
    assert all(rep_churn.dyn_results.values())

    # Wiring check with run_v1_to_v6_gates (policy="balanced" / "strict"):
    v_rep = run_v1_to_v6_gates(
        ir_churn,
        VerifierContext(profiles=profiles, policy="strict"),
        prover=DuckDbProverAdapter(),
        include_v6b=False,
    )
    assert v_rep.v6b_fail_count >= 1
    assert any(d.rule_id == "DV-AP-011" for d in v_rep.v6b_diagnostics)

    # 2. Un-promoted multi-active ballot sat -> DV-DYN-004 fails with counterexample
    ir_bad_sat = _build_clean_trap_ir(unpromote_ballot_mas_to_sat=True)
    rep_bad = DuckDbProverAdapter().prove(ir_bad_sat, profiles, source_rows_by_table=rows_map)
    assert rep_bad.dyn_results["DV-DYN-004"] is False
    assert any(ce.get("rule_id") == "DV-DYN-004" for ce in rep_bad.counterexamples)


def test_all_15_dv_dyn_rules_can_individually_fail_with_counterexamples() -> None:
    """Negative test proving every single DV-DYN-001..015 rule detects genuine violations."""
    profiles, rows_map = _load_trap_profiles_and_rows()
    ir = _build_clean_trap_ir()
    bundle = SqlGlotVaultCompiler("duckdb_local").compile(ir)

    def _fresh_populated_conn() -> duckdb.DuckDBPyConnection:
        c = duckdb.connect(":memory:")
        for s in bundle.ddl_statements:
            c.execute(s)
        for b in ir.bronze_tables:
            rows = rows_map[b.source_table_id]
            cols = [col.name for col in b.columns]
            c.executemany(
                f"INSERT INTO bronze.{b.table_name} ({', '.join(cols)}) VALUES ({', '.join(['?']*len(cols))})",
                [[r[col] for col in cols] for r in rows],
            )
        for s in bundle.dml_statements:
            c.execute(s)
        return c

    # Test each rule DV-DYN-001..015 individually
    summary_ok = AdversarialBatchSummary(batch2_deltas={}, boundary_shift_ok=True)

    # 001: Duplicate Hub BK / HK -> recreate hub_position without PK and insert dup
    c1 = _fresh_populated_conn()
    c1.execute("CREATE OR REPLACE TABLE raw_vault.hub_position AS SELECT * FROM raw_vault.hub_position UNION ALL SELECT * FROM raw_vault.hub_position LIMIT 10")
    res1, ce1 = run_all_dyn_invariants(c1, ir, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c1.close()
    assert res1["DV-DYN-001"] is False and ce1

    # 002: Hash collision (two different BKs with same HK)
    c2 = _fresh_populated_conn()
    c2.execute("CREATE OR REPLACE TABLE raw_vault.hub_position AS SELECT * FROM raw_vault.hub_position")
    c2.execute("INSERT INTO raw_vault.hub_position SELECT hk_position, bkcc, load_dts, rec_src, 'COLLIDING_BK' FROM raw_vault.hub_position LIMIT 1 OFFSET 2")
    res2, _ = run_all_dyn_invariants(c2, ir, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c2.close()
    assert res2["DV-DYN-002"] is False

    # 003: Link duplicate HK
    c3 = _fresh_populated_conn()
    c3.execute("CREATE OR REPLACE TABLE raw_vault.lnk_acct_mgr AS SELECT * FROM raw_vault.lnk_acct_mgr UNION ALL SELECT * FROM raw_vault.lnk_acct_mgr LIMIT 5")
    res3, _ = run_all_dyn_invariants(c3, ir, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c3.close()
    assert res3["DV-DYN-003"] is False

    # 004: Standard sat duplicate (hk, load_dts)
    c4 = _fresh_populated_conn()
    c4.execute("CREATE OR REPLACE TABLE raw_vault.sat_snap AS SELECT * FROM raw_vault.sat_snap UNION ALL SELECT * FROM raw_vault.sat_snap")
    res4, _ = run_all_dyn_invariants(c4, ir, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c4.close()
    assert res4["DV-DYN-004"] is False

    # 005: MAS duplicate (hk, load_dts, cdk)
    c5 = _fresh_populated_conn()
    c5.execute("CREATE OR REPLACE TABLE raw_vault.mas_ballots AS SELECT * FROM raw_vault.mas_ballots UNION ALL SELECT * FROM raw_vault.mas_ballots")
    res5, _ = run_all_dyn_invariants(c5, ir, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c5.close()
    assert res5["DV-DYN-005"] is False

    # 006: Orphan satellite row
    c6 = _fresh_populated_conn()
    c6.execute("INSERT INTO raw_vault.sat_snap VALUES (UNHEX(REPEAT('ab', 32)), TIMESTAMP '2026-01-01 00:00:00Z', 'X', UNHEX(REPEAT('ab', 32)), 1.0)")
    res6, _ = run_all_dyn_invariants(c6, ir, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c6.close()
    assert res6["DV-DYN-006"] is False

    # 007: Orphan link FK
    c7 = _fresh_populated_conn()
    c7.execute("INSERT INTO raw_vault.lnk_acct_mgr VALUES (UNHEX(REPEAT('cd', 32)), UNHEX(REPEAT('de', 32)), UNHEX(REPEAT('ef', 32)), TIMESTAMP '2026-01-01 00:00:00Z', 'X')")
    res7, _ = run_all_dyn_invariants(c7, ir, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c7.close()
    assert res7["DV-DYN-007"] is False

    # 008: Redundant consecutive hash_diff in satellite
    c8 = _fresh_populated_conn()
    c8.execute("INSERT INTO raw_vault.sat_snap SELECT hk_position, load_dts + INTERVAL '1 day', rec_src, hash_diff, quantity FROM raw_vault.sat_snap WHERE hk_position <> UNHEX(REPEAT('00', 32)) LIMIT 1")
    res8, _ = run_all_dyn_invariants(c8, ir, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c8.close()
    assert res8["DV-DYN-008"] is False

    # 009: Missing -1/-2 Zero-Key
    c9 = _fresh_populated_conn()
    c9.execute("DELETE FROM raw_vault.hub_position WHERE hk_position = UNHEX(REPEAT('ff', 32))")
    res9, _ = run_all_dyn_invariants(c9, ir, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c9.close()
    assert res9["DV-DYN-009"] is False

    # 010: Missing hub row from staging
    c10 = _fresh_populated_conn()
    c10.execute("DELETE FROM raw_vault.hub_position WHERE hk_position <> UNHEX(REPEAT('00', 32)) AND hk_position <> UNHEX(REPEAT('ff', 32))")
    res10, _ = run_all_dyn_invariants(c10, ir, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c10.close()
    assert res10["DV-DYN-010"] is False

    # 011: Stale satellite hash_diff mismatch vs staging
    c11 = _fresh_populated_conn()
    c11.execute("DELETE FROM raw_vault.sat_snap WHERE hk_position <> UNHEX(REPEAT('00', 32))")
    res11, _ = run_all_dyn_invariants(c11, ir, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c11.close()
    assert res11["DV-DYN-011"] is False

    # 012: Replay delta > 0
    c12 = _fresh_populated_conn()
    res12, _ = run_all_dyn_invariants(c12, ir, bundle.physical_aliases, bundle.stg_hd_col_map, AdversarialBatchSummary(batch2_deltas={"sat_snap": 2}))
    c12.close()
    assert res12["DV-DYN-012"] is False

    # 013: ESAT >1 active link at latest load_dts for same driving key + missing/link-resident DK checks
    c13 = _fresh_populated_conn()
    c13.execute("CREATE OR REPLACE TABLE raw_vault.esat_acct_mgr AS SELECT * FROM raw_vault.esat_acct_mgr")
    c13.execute("INSERT INTO raw_vault.esat_acct_mgr SELECT UNHEX(REPEAT('99', 32)), MAX(load_dts), 'X', hk_crm_acct, MAX(effective_from) FROM raw_vault.esat_acct_mgr WHERE hk_crm_acct <> UNHEX(REPEAT('00', 32)) GROUP BY hk_crm_acct LIMIT 1")
    res13, _ = run_all_dyn_invariants(c13, ir, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c13.close()
    assert res13["DV-DYN-013"] is False

    c13_empty_dk = _fresh_populated_conn()
    ir_empty_dk = ir.model_copy(
        update={
            "satellites": [
                s.model_copy(update={"driving_keys": []}) if s.sat_name == "esat_acct_mgr" else s
                for s in ir.satellites
            ]
        }
    )
    res13_empty, _ = run_all_dyn_invariants(c13_empty_dk, ir_empty_dk, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c13_empty_dk.close()
    assert res13_empty["DV-DYN-013"] is False

    c13_lnk_dk = _fresh_populated_conn()
    ir_lnk_dk = ir.model_copy(
        update={
            "satellites": [
                s.model_copy(update={"columns": [c for c in s.columns if c.name != "hk_crm_acct"]})
                if s.sat_name == "esat_acct_mgr"
                else s
                for s in ir.satellites
            ]
        }
    )
    res13_lnk_ok, _ = run_all_dyn_invariants(c13_lnk_dk, ir_lnk_dk, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    assert res13_lnk_ok["DV-DYN-013"] is True
    c13_lnk_dk.execute("CREATE OR REPLACE TABLE raw_vault.esat_acct_mgr AS SELECT * FROM raw_vault.esat_acct_mgr")
    c13_lnk_dk.execute("INSERT INTO raw_vault.lnk_acct_mgr SELECT UNHEX(REPEAT('99', 32)), hk_crm_acct, hk_manager, load_dts, 'X' FROM raw_vault.lnk_acct_mgr WHERE hk_crm_acct <> UNHEX(REPEAT('00', 32)) AND hk_crm_acct <> UNHEX(REPEAT('ff', 32)) LIMIT 1")
    c13_lnk_dk.execute("INSERT INTO raw_vault.esat_acct_mgr SELECT UNHEX(REPEAT('99', 32)), MAX(load_dts), 'X', hk_crm_acct, MAX(effective_from) FROM raw_vault.esat_acct_mgr WHERE hk_crm_acct <> UNHEX(REPEAT('00', 32)) GROUP BY hk_crm_acct LIMIT 1")
    res13_lnk_fail, _ = run_all_dyn_invariants(c13_lnk_dk, ir_lnk_dk, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c13_lnk_dk.close()
    assert res13_lnk_fail["DV-DYN-013"] is False

    # 014: Zero-key leaked into information_mart AND into Z3 PIT
    c14 = _fresh_populated_conn()
    c14.execute("CREATE OR REPLACE VIEW information_mart.vw_crm_acct_current AS SELECT hk_crm_acct, account_id, CAST(0 AS DECIMAL(18,2)) AS daily_nav_usd FROM raw_vault.hub_crm_acct")
    res14, _ = run_all_dyn_invariants(c14, ir, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c14.close()
    assert res14["DV-DYN-014"] is False

    c14_z3 = _fresh_populated_conn()
    c14_z3.execute(
        "INSERT INTO business_vault.pit_trade VALUES ("
        "UNHEX(REPEAT('00', 32)), TIMESTAMP '2026-10-20 00:00:00Z', "
        "UNHEX(REPEAT('00', 32)), TIMESTAMP '1900-01-01 00:00:00Z', TIMESTAMP '1900-01-01 00:00:00Z')"
    )
    res14_z3, _ = run_all_dyn_invariants(c14_z3, ir, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c14_z3.close()
    assert res14_z3["DV-DYN-014"] is False

    # 015: PIT broken pointer, empty PIT, and failed Batch-4 bitemporal proof
    c15 = _fresh_populated_conn()
    c15.execute(
        "INSERT INTO business_vault.pit_trade VALUES ("
        "UNHEX(REPEAT('aa', 32)), TIMESTAMP '2026-10-20 00:00:00Z', "
        "UNHEX(REPEAT('aa', 32)), TIMESTAMP '2026-10-20 00:00:00Z', TIMESTAMP '2026-10-20 00:00:00Z')"
    )
    res15, _ = run_all_dyn_invariants(c15, ir, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c15.close()
    assert res15["DV-DYN-015"] is False

    c15_empty = _fresh_populated_conn()
    c15_empty.execute("DELETE FROM business_vault.pit_trade")
    res15_empty, _ = run_all_dyn_invariants(c15_empty, ir, bundle.physical_aliases, bundle.stg_hd_col_map, summary_ok)
    c15_empty.close()
    assert res15_empty["DV-DYN-015"] is False

    c15_bitemp = _fresh_populated_conn()
    res15_bitemp, _ = run_all_dyn_invariants(
        c15_bitemp, ir, bundle.physical_aliases, bundle.stg_hd_col_map, AdversarialBatchSummary(bitemporal_proof_ok=False)
    )
    c15_bitemp.close()
    assert res15_bitemp["DV-DYN-015"] is False


def test_alg_a6_sql_purity_catches_violations() -> None:
    """Verify Alg A.6 violations populate ast_violations cleanly."""
    verifier = SqlAstVerifier()
    assert verifier.verify_sql("SELECT * FROM bronze.tbl", "duckdb")
    assert verifier.verify_sql(
        "CREATE VIEW staging.stg_x AS SELECT UNHEX(SHA256(CAST(STRLEN(a) AS VARCHAR))) AS hk_x FROM bronze.t",
        "duckdb",
    )
    assert verifier.verify_sql(
        "INSERT INTO raw_vault.sat_x SELECT hk_x, MIN(load_dts) FROM staging.stg_x GROUP BY hk_x",
        "duckdb",
    )
    assert verifier.verify_sql(
        "INSERT INTO raw_vault.lnk_doc (hk_doc, load_dts) SELECT hk_doc, load_dts FROM staging.stg_doc",
        "duckdb",
        extra_unstructured_links=["lnk_doc"],
    )


def test_dv_dyn_011_honors_dedup_order_cols_and_wave2_nav_restatements() -> None:
    """Verify DV-DYN-004 and DV-DYN-011 both pass on intra-batch dedup_order_cols tie-breaks and 180-row W2 NAV CSV."""
    import csv

    # 1. Minimal intra-batch tie-break IR (dedup_order_cols=["prio"])
    b_cols = [
        IRField(field_id=1, name="k", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="prio", logical_type="INT32", nullable=False, is_pii=False),
        IRField(field_id=3, name="val", logical_type="UTF8_STRING", nullable=False, is_pii=False),
    ]
    r_hk = HashRecipeSpec(
        target_col="hk_t",
        recipe_kind="HUB_HK",
        bkcc_literal="GLOBAL",
        source_columns=["k"],
        sort_alphabetically=False,
        key_columns=["k"],
    )
    r_hd = HashRecipeSpec(
        target_col="hash_diff",
        recipe_kind="SAT_HASH_DIFF",
        source_columns=["prio", "val"],
        sort_alphabetically=True,
        key_columns=["hk_t"],
    )
    ir_tie = CertifiedVaultIR(
        project_id="proj_tie",
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[
            BronzeTableSpec(
                table_name="bronze_tie",
                source_id="src_tie",
                source_table_id="src_tie:tie",
                columns=b_cols,
            )
        ],
        staging_views=[
            StagingViewSpec(
                view_name="stg_tie",
                source_id="src_tie",
                source_table_id="src_tie:tie",
                col_lineage_mappings={c.name: c.name for c in b_cols},
                hash_recipes=[r_hk, r_hd],
                projected_fields=b_cols,
            )
        ],
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_tie",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id="src_tie:tie",
                        col_name="k",
                        source_id="src_tie",
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[
                    SourceBindingSpec(
                        source_id="src_tie",
                        table_id="src_tie:tie",
                        col_mappings={},
                        hash_recipe=r_hk,
                    )
                ],
                columns=[
                    IRField(field_id=1, name="hk_t", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="k", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_hk,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_t"]),
            )
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name="sat_tie",
                sat_kind="STANDARD",
                parent_name="hub_tie",
                source_id="src_tie",
                source_table_id="src_tie:tie",
                load_semantics=LoadSemanticsSpec(
                    source_table_id="src_tie:tie",
                    dedup_order_cols=["prio"],
                ),
                attributes=[
                    DraftField(name="prio", logical_type="INT32", nullable=False, is_pii=False),
                    DraftField(name="val", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                columns=[
                    IRField(field_id=1, name="hk_t", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="prio", logical_type="INT32", nullable=False, is_pii=False),
                    IRField(field_id=6, name="val", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_hd,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_t", "load_dts"]),
            )
        ],
    )
    tie_rows = [
        {"k": "K1", "prio": 1, "val": "LOSER"},
        {"k": "K1", "prio": 99, "val": "WINNER"},
    ]
    tie_rep = DuckDbProverAdapter().prove(
        ir_tie, [], source_rows_by_table={"src_tie:tie": tie_rows}
    )
    assert tie_rep.dyn_results["DV-DYN-004"] is True
    assert tie_rep.dyn_results["DV-DYN-011"] is True
    assert all(tie_rep.dyn_results.values()), tie_rep.counterexamples

    # 2. Full 180-row wave2_bitemporal_nav_restatements.csv with dedup_order_cols=["nav_per_share"]
    w2_nav_path = REPO_ROOT / "data/public_am/wave2_bitemporal_nav_restatements.csv"
    assert w2_nav_path.exists()
    with w2_nav_path.open("r", encoding="utf-8", newline="") as fh:
        nav_rows = list(csv.DictReader(fh))
    assert len(nav_rows) == 180

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
        target_col="hk_fund_nav",
        recipe_kind="HUB_HK",
        bkcc_literal="GLOBAL",
        source_columns=["fund_code", "share_class", "as_of_date"],
        sort_alphabetically=False,
        key_columns=["fund_code", "share_class", "as_of_date"],
    )
    hd_nav = HashRecipeSpec(
        target_col="hash_diff",
        recipe_kind="SAT_HASH_DIFF",
        source_columns=["aum_usd", "nav_per_share", "restatement_reason"],
        sort_alphabetically=True,
        key_columns=["hk_fund_nav"],
    )
    nav_ir = CertifiedVaultIR(
        project_id="proj_nav_dedup",
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[
            BronzeTableSpec(
                table_name="bronze_w2_nav",
                source_id="w2_nav",
                source_table_id="w2_nav:wave2_bitemporal_nav_restatements",
                columns=b_nav_cols,
            )
        ],
        staging_views=[
            StagingViewSpec(
                view_name="stg_w2_nav",
                source_id="w2_nav",
                source_table_id="w2_nav:wave2_bitemporal_nav_restatements",
                col_lineage_mappings={c.name: c.name for c in b_nav_cols},
                hash_recipes=[r_nav, hd_nav],
                projected_fields=b_nav_cols,
            )
        ],
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_fund_nav",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(table_id="w2_nav:wave2_bitemporal_nav_restatements", col_name="fund_code", source_id="w2_nav", key_tier="TIER_2_ENTERPRISE_SOR", bkcc_value="GLOBAL"),
                    BusinessKeyPart(table_id="w2_nav:wave2_bitemporal_nav_restatements", col_name="share_class", source_id="w2_nav", key_tier="TIER_2_ENTERPRISE_SOR", bkcc_value="GLOBAL"),
                    BusinessKeyPart(table_id="w2_nav:wave2_bitemporal_nav_restatements", col_name="as_of_date", source_id="w2_nav", key_tier="TIER_2_ENTERPRISE_SOR", bkcc_value="GLOBAL"),
                ],
                source_bindings=[
                    SourceBindingSpec(
                        source_id="w2_nav",
                        table_id="w2_nav:wave2_bitemporal_nav_restatements",
                        col_mappings={},
                        hash_recipe=r_nav,
                    )
                ],
                columns=[
                    IRField(field_id=1, name="hk_fund_nav", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="fund_code", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=6, name="share_class", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=7, name="as_of_date", logical_type="DATE32", nullable=False, is_pii=False),
                ],
                hash_recipe=r_nav,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_fund_nav"]),
            )
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name="sat_nav_bitemp",
                sat_kind="STANDARD",
                parent_name="hub_fund_nav",
                source_id="w2_nav",
                source_table_id="w2_nav:wave2_bitemporal_nav_restatements",
                is_bitemporal=True,
                applied_dts_col="applied_dts",
                load_semantics=LoadSemanticsSpec(
                    source_table_id="w2_nav:wave2_bitemporal_nav_restatements",
                    dedup_order_cols=["nav_per_share"],
                ),
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
                hash_recipe=hd_nav,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_fund_nav", "load_dts"]),
            )
        ],
        pit_bridges=[
            PitBridgeSpec(
                object_name="pit_nav",
                object_kind="PIT",
                root_hub="hub_fund_nav",
                target_satellites_or_links=["sat_nav_bitemp"],
                grain_cols=["hk_fund_nav", "snapshot_dts"],
            )
        ],
    )
    nav_rep = DuckDbProverAdapter().prove(
        nav_ir, [], source_rows_by_table={"w2_nav:wave2_bitemporal_nav_restatements": nav_rows}
    )
    assert nav_rep.dyn_results["DV-DYN-004"] is True
    assert nav_rep.dyn_results["DV-DYN-011"] is True
    assert all(nav_rep.dyn_results.values()), (
        {k: v for k, v in nav_rep.dyn_results.items() if not v},
        nav_rep.counterexamples,
    )

    # 3. dedup_order_cols placed exclusively on sat.source_bindings[0].load_semantics (not sat.load_semantics)
    sb_dedup_ir = ir_tie.model_copy(
        update={
            "satellites": [
                s.model_copy(
                    update={
                        "load_semantics": None,
                        "source_bindings": [
                            SourceBindingSpec(
                                source_id="src_tie",
                                table_id="src_tie:tie",
                                col_mappings={},
                                load_semantics=LoadSemanticsSpec(
                                    source_table_id="src_tie:tie",
                                    dedup_order_cols=["prio"],
                                ),
                            )
                        ],
                    }
                )
                for s in ir_tie.satellites
            ]
        }
    )
    sb_rep = DuckDbProverAdapter().prove(
        sb_dedup_ir, [], source_rows_by_table={"src_tie:tie": tie_rows}
    )
    assert sb_rep.dyn_results["DV-DYN-004"] is True
    assert sb_rep.dyn_results["DV-DYN-011"] is True
    assert all(sb_rep.dyn_results.values()), sb_rep.counterexamples


def test_dv_dyn_010_011_honor_review_status_and_filter_ast() -> None:
    """Verify DV-DYN-010 (non-unstructured Link) and DV-DYN-004/011 (Satellite) honor APPROVED & filter_ast."""
    from datetime import date
    from decimal import Decimal
    from tests.storage.test_11_target_eval_gate import _build_enterprise_11type_ir

    # 1. DV-DYN-010 on _build_enterprise_11type_ir().t_lnk_trade_exec (TRANSACTIONAL + APPROVED)
    ent_ir = _build_enterprise_11type_ir()
    ent_rows = [
        {
            "issuer_cik": "0000320193",
            "account_id": "ACCT-01",
            "bkcc_src": "IBOR",
            "alias_cik": "0000320193-ALT",
            "proposal_seq": 1,
            "owner_email": "ir@apple.com",
            "trade_qty": 100,
            "steward_gate_status": "APPROVED",
            "issuer_name": "Apple Inc",
            "seat_count": 8,
            "market_cap": 3000000000000,
            "nav_dec": Decimal("216.2800"),
            "founded_dt": date(1976, 4, 1),
            "updated_ts": datetime(2026, 10, 20, 18, 0, 0, tzinfo=UTC),
            "is_active": True,
            "meta_json": '{"sector": "tech"}',
            "tags_arr": ["tech"],
            "attrs_map": {"tier": 1},
            "nested_arr": [["a"]],
            "nested_map": {"k": [1]},
        },
        {
            "issuer_cik": "0000789019",
            "account_id": "ACCT-02",
            "bkcc_src": "SEC",
            "alias_cik": "0000789019-ALT",
            "proposal_seq": 2,
            "owner_email": "ir@msft.com",
            "trade_qty": 250,
            "steward_gate_status": "PENDING",
            "issuer_name": "Nordisk Suverän Fond",
            "seat_count": 12,
            "market_cap": 2800000000000,
            "nav_dec": Decimal("412.5000"),
            "founded_dt": date(1975, 4, 4),
            "updated_ts": datetime(2026, 10, 21, 9, 30, 0, tzinfo=UTC),
            "is_active": False,
            "meta_json": '{"sector": "cloud"}',
            "tags_arr": ["cloud"],
            "attrs_map": {"tier": 2},
            "nested_arr": [["b"]],
            "nested_map": {"k": [2]},
        },
    ]
    ent_rep = DuckDbProverAdapter().prove(
        ent_ir, [], source_rows_by_table={"src_ibor:issuer_feed": ent_rows}
    )
    assert ent_rep.dyn_results["DV-DYN-010"] is True
    assert all(ent_rep.dyn_results.values()), (
        {k: v for k, v in ent_rep.dyn_results.items() if not v},
        ent_rep.counterexamples,
    )

    # 2. DV-DYN-004 & DV-DYN-011 on Satellite with require_review_status='APPROVED' and filter_ast
    b_cols = [
        IRField(field_id=1, name="k", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="steward_gate_status", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=3, name="status_flag", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=4, name="val", logical_type="UTF8_STRING", nullable=False, is_pii=False),
    ]
    r_hk = HashRecipeSpec(
        target_col="hk_t", recipe_kind="HUB_HK", bkcc_literal="GLOBAL",
        source_columns=["k"], sort_alphabetically=False, key_columns=["k"],
    )
    r_hd = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=["val"], sort_alphabetically=True, key_columns=["hk_t"],
    )

    def _sat_ir(sem: LoadSemanticsSpec) -> CertifiedVaultIR:
        return CertifiedVaultIR(
            project_id="proj_sat_flt",
            ontology_version="1.0.0",
            wave_number=1,
            bronze_tables=[
                BronzeTableSpec(
                    table_name="bronze_flt", source_id="src_flt",
                    source_table_id="src_flt:flt", columns=b_cols,
                )
            ],
            staging_views=[
                StagingViewSpec(
                    view_name="stg_flt", source_id="src_flt",
                    source_table_id="src_flt:flt",
                    col_lineage_mappings={c.name: c.name for c in b_cols},
                    hash_recipes=[r_hk, r_hd], projected_fields=b_cols,
                )
            ],
            hubs=[
                CertifiedHubSpec(
                    hub_name="hub_flt", key_tier="TIER_1_GLOBAL_STANDARD",
                    business_keys=[
                        BusinessKeyPart(
                            table_id="src_flt:flt", col_name="k", source_id="src_flt",
                            key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL",
                        )
                    ],
                    source_bindings=[
                        SourceBindingSpec(
                            source_id="src_flt", table_id="src_flt:flt",
                            col_mappings={}, hash_recipe=r_hk,
                        )
                    ],
                    columns=[
                        IRField(field_id=1, name="hk_t", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                        IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                        IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                        IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                        IRField(field_id=5, name="k", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    ],
                    hash_recipe=r_hk, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_t"]),
                )
            ],
            satellites=[
                CertifiedSatelliteSpec(
                    sat_name="sat_flt", sat_kind="STANDARD", parent_name="hub_flt",
                    source_id="src_flt", source_table_id="src_flt:flt",
                    load_semantics=sem,
                    attributes=[DraftField(name="val", logical_type="UTF8_STRING", nullable=False, is_pii=False)],
                    columns=[
                        IRField(field_id=1, name="hk_t", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                        IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                        IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                        IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                        IRField(field_id=5, name="val", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    ],
                    hash_recipe=r_hd, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_t", "load_dts"]),
                )
            ],
        )

    # 2a. require_review_status="APPROVED" with APPROVED + PENDING rows (including same-key pending variant)
    rev_ir = _sat_ir(LoadSemanticsSpec(source_table_id="src_flt:flt", require_review_status="APPROVED"))
    rev_rows = [
        {"k": "K1", "steward_gate_status": "APPROVED", "status_flag": "OK", "val": "V1"},
        {"k": "K1", "steward_gate_status": "PENDING", "status_flag": "OK", "val": "V1_PENDING"},
        {"k": "K2", "steward_gate_status": "PENDING", "status_flag": "OK", "val": "V2_PENDING"},
    ]
    rev_rep = DuckDbProverAdapter().prove(rev_ir, [], source_rows_by_table={"src_flt:flt": rev_rows})
    assert rev_rep.dyn_results["DV-DYN-004"] is True
    assert rev_rep.dyn_results["DV-DYN-011"] is True
    assert all(rev_rep.dyn_results.values()), rev_rep.counterexamples

    # 2b. filter_ast with OK + REJECTED rows (including same-key rejected variant)
    flt_ir = _sat_ir(
        LoadSemanticsSpec(
            source_table_id="src_flt:flt",
            filter_ast=PredicateLeaf(op="EQ", column="status_flag", literal="OK"),
        )
    )
    flt_rows = [
        {"k": "K1", "steward_gate_status": "APPROVED", "status_flag": "OK", "val": "V1"},
        {"k": "K1", "steward_gate_status": "APPROVED", "status_flag": "REJECTED", "val": "V1_REJ"},
        {"k": "K2", "steward_gate_status": "APPROVED", "status_flag": "REJECTED", "val": "V2_REJ"},
    ]
    flt_rep = DuckDbProverAdapter().prove(flt_ir, [], source_rows_by_table={"src_flt:flt": flt_rows})
    assert flt_rep.dyn_results["DV-DYN-004"] is True
    assert flt_rep.dyn_results["DV-DYN-011"] is True
    assert all(flt_rep.dyn_results.values()), flt_rep.counterexamples


def test_batch3_churn_and_counterexamples_with_composite_timestamptz() -> None:
    """Verify Batch-3 detect_batch3_churn and query_counterexamples handle composite TIMESTAMPTZ without BinderException."""
    from opendv.models.vault_ir import ArrayType, MapType
    from opendv.storage.prover._dyn_sat_checks import query_counterexamples

    cols = [
        IRField(field_id=1, name="item_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="etl_snapshot_ts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
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
        source_columns=["etl_snapshot_ts", "ts_arr", "ts_map"],
        sort_alphabetically=True,
        key_columns=["hk_item"],
    )
    ir = CertifiedVaultIR(
        project_id="proj_b3_tz",
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[BronzeTableSpec(table_name="bronze_item", source_id="s1", source_table_id="s1:item", columns=cols)],
        staging_views=[
            StagingViewSpec(
                view_name="stg_item",
                source_id="s1",
                source_table_id="s1:item",
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
                        table_id="s1:item",
                        col_name="item_id",
                        source_id="s1",
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[SourceBindingSpec(source_id="s1", table_id="s1:item", col_mappings={}, hash_recipe=hk_r)],
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
                sat_name="sat_item",
                sat_kind="STANDARD",
                parent_name="hub_item",
                source_id="s1",
                source_table_id="s1:item",
                attributes=[
                    DraftField(name="etl_snapshot_ts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    DraftField(name="ts_arr", logical_type=ArrayType(element_type="TIMESTAMP_UTC_US"), nullable=True, is_pii=False),
                    DraftField(name="ts_map", logical_type=MapType(key_type="UTF8_STRING", value_type="TIMESTAMP_UTC_US"), nullable=True, is_pii=False),
                ],
                columns=[
                    IRField(field_id=1, name="hk_item", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="etl_snapshot_ts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=6, name="ts_arr", logical_type=ArrayType(element_type="TIMESTAMP_UTC_US"), nullable=True, is_pii=False),
                    IRField(field_id=7, name="ts_map", logical_type=MapType(key_type="UTF8_STRING", value_type="TIMESTAMP_UTC_US"), nullable=True, is_pii=False),
                ],
                hash_recipe=hd_r,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item", "load_dts"]),
            )
        ],
    )

    rep = DuckDbProverAdapter().prove(ir, [])
    assert len(rep.dyn_results) == 15 and all(rep.dyn_results.values()), rep.counterexamples
    assert rep.ast_violations == []
    assert any(d.rule_id == "DV-AP-011" and d.counterexample_rows for d in rep.empirical_smells)

    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE ce_tz (ts TIMESTAMPTZ, arr TIMESTAMPTZ[], mp MAP(VARCHAR, TIMESTAMPTZ))"
        )
        conn.execute(
            "INSERT INTO ce_tz VALUES (TIMESTAMPTZ '2026-10-20 18:00:00Z', "
            "[TIMESTAMPTZ '2026-10-20 18:00:00Z'], MAP(['k'], [TIMESTAMPTZ '2026-10-20 18:00:00Z']))"
        )
        ce_rows = query_counterexamples(conn, "SELECT ts, arr, mp FROM ce_tz", set())
        assert len(ce_rows) == 1
        assert ce_rows[0]["ts"] == "2026-10-20T18:00:00.000000Z"
        assert isinstance(ce_rows[0]["arr"], str) and isinstance(ce_rows[0]["mp"], str)
    finally:
        conn.close()


def test_iter16_prover_array_of_map_and_nonnull_collections() -> None:
    """Verify DuckDbProverAdapter handles ArrayType(MapType) source_rows_by_table and nullable=False collections."""
    from opendv.models.vault_ir import ArrayType, MapType, PortableLogicalType

    def _coll_prover_ir(ltype: PortableLogicalType, *, nullable: bool) -> CertifiedVaultIR:
        cols = [
            IRField(field_id=1, name="item_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
            IRField(field_id=2, name="c", logical_type=ltype, nullable=nullable, is_pii=False),
        ]
        hk_r = HashRecipeSpec(
            target_col="hk_item", recipe_kind="HUB_HK", bkcc_literal="GLOBAL",
            source_columns=["item_id"], sort_alphabetically=False, key_columns=["item_id"],
        )
        hd_r = HashRecipeSpec(
            target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
            source_columns=["c"], sort_alphabetically=True, key_columns=["hk_item"],
        )
        return CertifiedVaultIR(
            project_id="proj_p16", ontology_version="1.0.0", wave_number=1,
            bronze_tables=[BronzeTableSpec(table_name="bronze_items", source_id="src", source_table_id="src:items", columns=cols)],
            staging_views=[StagingViewSpec(view_name="stg_items", source_id="src", source_table_id="src:items", col_lineage_mappings={"item_id": "item_id", "c": "c"}, hash_recipes=[hk_r, hd_r], projected_fields=cols)],
            hubs=[CertifiedHubSpec(
                hub_name="hub_item", key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[BusinessKeyPart(table_id="src:items", col_name="item_id", source_id="src", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
                source_bindings=[SourceBindingSpec(source_id="src", table_id="src:items", col_mappings={}, hash_recipe=hk_r)],
                columns=[
                    IRField(field_id=1, name="hk_item", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="item_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=hk_r, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item"]),
            )],
            satellites=[CertifiedSatelliteSpec(
                sat_name="sat_items", sat_kind="STANDARD", parent_name="hub_item", source_id="src", source_table_id="src:items",
                attributes=[DraftField(name="c", logical_type=ltype, nullable=nullable, is_pii=False)],
                columns=[
                    IRField(field_id=1, name="hk_item", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="c", logical_type=ltype, nullable=nullable, is_pii=False),
                ],
                hash_recipe=hd_r, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item", "load_dts"]),
            )],
        )

    ir_am = _coll_prover_ir(ArrayType(element_type=MapType(key_type="UTF8_STRING", value_type="INT64")), nullable=True)
    rep_am = DuckDbProverAdapter().prove(
        ir_am,
        [],
        source_rows_by_table={
            "src:items": [
                {"item_id": "I1", "c": [{"z": 1, "a": 2}, None, {}]},
                {"item_id": "I2", "c": []},
                {"item_id": "I3", "c": None},
            ]
        },
    )
    assert rep_am.ast_violations == []
    assert len(rep_am.dyn_results) == 15 and all(rep_am.dyn_results.values()), rep_am.counterexamples

    for lt in (
        ArrayType(element_type="UTF8_STRING"),
        MapType(key_type="UTF8_STRING", value_type="INT64"),
        ArrayType(element_type=MapType(key_type="UTF8_STRING", value_type="INT64")),
    ):
        rep_nn = DuckDbProverAdapter().prove(_coll_prover_ir(lt, nullable=False), [])
        assert rep_nn.ast_violations == []
        assert len(rep_nn.dyn_results) == 15 and all(rep_nn.dyn_results.values()), (lt, rep_nn.counterexamples)


def test_iter10_batch3_null_timestamptz_churn_and_bitemporal_load_semantics() -> None:
    """Verify Batch-3 handles NULL TIMESTAMP_UTC_US tech cols and Batch-4 honors bitemporal load_semantics."""
    b_cols = [
        IRField(field_id=1, name="k", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="applied_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
        IRField(field_id=3, name="etl_snapshot_ts", logical_type="TIMESTAMP_UTC_US", nullable=True, is_pii=False),
        IRField(field_id=4, name="steward_gate_status", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=5, name="status_flag", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=6, name="val", logical_type="UTF8_STRING", nullable=False, is_pii=False),
    ]
    r_hk = HashRecipeSpec(
        target_col="hk_t", recipe_kind="HUB_HK", bkcc_literal="GLOBAL",
        source_columns=["k"], sort_alphabetically=False, key_columns=["k"],
    )
    r_hd = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=["etl_snapshot_ts", "val"], sort_alphabetically=True, key_columns=["hk_t"],
    )
    sem = LoadSemanticsSpec(
        source_table_id="s1:bt",
        filter_ast=PredicateLeaf(op="EQ", column="status_flag", literal="OK"),
        require_review_status="APPROVED",
    )
    ir = CertifiedVaultIR(
        project_id="proj_iter10_bt",
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[
            BronzeTableSpec(
                table_name="bronze_bt", source_id="s1", source_table_id="s1:bt", columns=b_cols,
            )
        ],
        staging_views=[
            StagingViewSpec(
                view_name="stg_bt", source_id="s1", source_table_id="s1:bt",
                col_lineage_mappings={c.name: c.name for c in b_cols},
                hash_recipes=[r_hk, r_hd], projected_fields=b_cols,
            )
        ],
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_bt", key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id="s1:bt", col_name="k", source_id="s1",
                        key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[
                    SourceBindingSpec(source_id="s1", table_id="s1:bt", col_mappings={}, hash_recipe=r_hk)
                ],
                columns=[
                    IRField(field_id=1, name="hk_t", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="k", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_hk, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_t"]),
            )
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name="sat_bt", sat_kind="STANDARD", parent_name="hub_bt",
                source_id="s1", source_table_id="s1:bt",
                is_bitemporal=True, applied_dts_col="applied_dts",
                load_semantics=sem,
                attributes=[
                    DraftField(name="etl_snapshot_ts", logical_type="TIMESTAMP_UTC_US", nullable=True, is_pii=False),
                    DraftField(name="val", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                columns=[
                    IRField(field_id=1, name="hk_t", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="applied_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=6, name="etl_snapshot_ts", logical_type="TIMESTAMP_UTC_US", nullable=True, is_pii=False),
                    IRField(field_id=7, name="val", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_hd, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_t", "load_dts"]),
            )
        ],
        pit_bridges=[
            PitBridgeSpec(
                object_name="pit_bt", object_kind="PIT", root_hub="hub_bt",
                target_satellites_or_links=["sat_bt"], grain_cols=["hk_t", "snapshot_dts"],
            )
        ],
    )
    rows = [
        # Earliest row by applied_dts is REJECTED / PENDING -> must be skipped by Batch-4 restatement clone
        {
            "k": "K1",
            "applied_dts": datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
            "etl_snapshot_ts": None,
            "steward_gate_status": "PENDING",
            "status_flag": "REJECTED",
            "val": "V_IGNORED",
        },
        {
            "k": "K2",
            "applied_dts": datetime(2026, 1, 5, 0, 0, 0, tzinfo=UTC),
            "etl_snapshot_ts": None,
            "steward_gate_status": "APPROVED",
            "status_flag": "OK",
            "val": "V_ACCEPTED",
        },
    ]
    rep = DuckDbProverAdapter().prove(ir, [], source_rows_by_table={"s1:bt": rows})
    assert rep.dyn_results["DV-DYN-015"] is True, rep.counterexamples
    assert all(rep.dyn_results.values()), rep.counterexamples

    # Unfiltered IR with all-NULL TIMESTAMP_UTC_US tech col triggers Batch-3 DV-AP-011
    ir_unfiltered = ir.model_copy(
        update={
            "satellites": [s.model_copy(update={"load_semantics": None}) for s in ir.satellites]
        }
    )
    rep_churn = DuckDbProverAdapter().prove(ir_unfiltered, [], source_rows_by_table={"s1:bt": rows})
    assert all(rep_churn.dyn_results.values()), rep_churn.counterexamples
    assert any(d.rule_id == "DV-AP-011" for d in rep_churn.empirical_smells), rep_churn.empirical_smells


def test_iter12_prover_non_string_masked_pii_samples() -> None:
    """Non-string PII columns (INT64, DecimalType, DATE32, BOOLEAN, JSON_DOCUMENT) with <MASKED_PII> samples must not crash DuckDB."""
    b_cols = [
        IRField(field_id=1, name="acct_id", logical_type="UTF8_STRING", nullable=False, is_pii=True),
        IRField(field_id=2, name="tax_num", logical_type="INT64", nullable=False, is_pii=True),
        IRField(field_id=3, name="net_worth", logical_type=DecimalType(precision=18, scale=2), nullable=True, is_pii=True),
        IRField(field_id=4, name="dob", logical_type="DATE32", nullable=True, is_pii=True),
        IRField(field_id=5, name="is_vip", logical_type="BOOLEAN", nullable=True, is_pii=True),
        IRField(field_id=6, name="meta_json", logical_type="JSON_DOCUMENT", nullable=True, is_pii=True),
    ]
    r_hk = HashRecipeSpec(
        target_col="hk_acct", recipe_kind="HUB_HK", bkcc_literal="GLOBAL",
        source_columns=["acct_id"], sort_alphabetically=False, key_columns=["acct_id"],
    )
    r_hd = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=["dob", "is_vip", "meta_json", "net_worth", "tax_num"],
        sort_alphabetically=True, key_columns=["hk_acct"],
    )
    ir = CertifiedVaultIR(
        project_id="proj_pii_num",
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[
            BronzeTableSpec(table_name="bronze_pii", source_id="s1", source_table_id="s1:t", columns=b_cols)
        ],
        staging_views=[
            StagingViewSpec(
                view_name="stg_pii", source_id="s1", source_table_id="s1:t",
                col_lineage_mappings={c.name: c.name for c in b_cols},
                hash_recipes=[r_hk, r_hd], projected_fields=b_cols,
            )
        ],
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_acct", key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id="s1:t", col_name="acct_id", source_id="s1",
                        key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[SourceBindingSpec(source_id="s1", table_id="s1:t", col_mappings={}, hash_recipe=r_hk)],
                columns=[
                    IRField(field_id=1, name="hk_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="acct_id", logical_type="UTF8_STRING", nullable=False, is_pii=True),
                ],
                hash_recipe=r_hk, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_acct"]),
            )
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name="sat_pii_num", sat_kind="PII", parent_name="hub_acct",
                source_id="s1", source_table_id="s1:t",
                attributes=[
                    DraftField(name="tax_num", logical_type="INT64", nullable=False, is_pii=True),
                    DraftField(name="net_worth", logical_type=DecimalType(precision=18, scale=2), nullable=True, is_pii=True),
                    DraftField(name="dob", logical_type="DATE32", nullable=True, is_pii=True),
                    DraftField(name="is_vip", logical_type="BOOLEAN", nullable=True, is_pii=True),
                    DraftField(name="meta_json", logical_type="JSON_DOCUMENT", nullable=True, is_pii=True),
                ],
                columns=[
                    IRField(field_id=1, name="hk_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="tax_num", logical_type="INT64", nullable=False, is_pii=True),
                    IRField(field_id=6, name="net_worth", logical_type=DecimalType(precision=18, scale=2), nullable=True, is_pii=True),
                    IRField(field_id=7, name="dob", logical_type="DATE32", nullable=True, is_pii=True),
                    IRField(field_id=8, name="is_vip", logical_type="BOOLEAN", nullable=True, is_pii=True),
                    IRField(field_id=9, name="meta_json", logical_type="JSON_DOCUMENT", nullable=True, is_pii=True),
                ],
                hash_recipe=r_hd, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_acct", "load_dts"]),
            )
        ],
    )
    prof = SourceProfile(
        source_id="s1",
        schema_fingerprint="fp_pii",
        table_count=1,
        tables=[
            TableProfile(
                table_id="s1:t",
                table_name="t",
                row_count_est=3,
                columns=[
                    ColumnProfile(
                        col_name="acct_id", sql_type="VARCHAR", nullable=False, is_pk=True,
                        null_ratio=0.0, distinct_count_est=3, pii_tags=["ID"],
                        sample_values=["<MASKED_PII>", "<MASKED_PII>", "<MASKED_PII>"],
                    ),
                    ColumnProfile(
                        col_name="tax_num", sql_type="BIGINT", nullable=False, is_pk=False,
                        null_ratio=0.0, distinct_count_est=3, pii_tags=["TAX_ID"],
                        sample_values=["<MASKED_PII>", "<MASKED_PII>", "<MASKED_PII>"],
                    ),
                    ColumnProfile(
                        col_name="net_worth", sql_type="DECIMAL(18,2)", nullable=True, is_pk=False,
                        null_ratio=0.0, distinct_count_est=3, pii_tags=["INCOME"],
                        sample_values=["<MASKED_PII>", "<MASKED_PII>"],
                    ),
                    ColumnProfile(
                        col_name="dob", sql_type="DATE", nullable=True, is_pk=False,
                        null_ratio=0.0, distinct_count_est=3, pii_tags=["DOB"],
                        sample_values=["<MASKED_PII>", "<MASKED_PII>"],
                    ),
                    ColumnProfile(
                        col_name="is_vip", sql_type="BOOLEAN", nullable=True, is_pk=False,
                        null_ratio=0.0, distinct_count_est=2, pii_tags=["FLAG"],
                        sample_values=["<MASKED_PII>", "<MASKED_PII>"],
                    ),
                    ColumnProfile(
                        col_name="meta_json", sql_type="JSON", nullable=True, is_pk=False,
                        null_ratio=0.0, distinct_count_est=3, pii_tags=["META"],
                        sample_values=["<MASKED_PII>", "<MASKED_PII>"],
                    ),
                ],
            )
        ],
    )
    rep = DuckDbProverAdapter().prove(ir, [prof])
    assert len(rep.dyn_results) == 15
    assert all(rep.dyn_results.values()), (rep.dyn_results, rep.counterexamples)


def test_probe_batch4_bitemporal_varchar_iso_date() -> None:
    """Verify Batch-4 bitemporal proof handles UTF8_STRING applied_dts_col (SQLite TEXT dates)."""
    b_cols = [
        IRField(field_id=1, name="curve_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="observation_date", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=3, name="rate_2y", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
    ]
    r_hk = HashRecipeSpec(
        target_col="hk_yield_curve",
        recipe_kind="HUB_HK",
        bkcc_literal="GLOBAL",
        source_columns=["curve_id"],
        sort_alphabetically=False,
        key_columns=["curve_id"],
    )
    r_hd = HashRecipeSpec(
        target_col="hash_diff",
        recipe_kind="SAT_HASH_DIFF",
        source_columns=["observation_date", "rate_2y"],
        sort_alphabetically=True,
        key_columns=["hk_yield_curve"],
    )
    ir = CertifiedVaultIR(
        project_id="proj_bitemp_varchar",
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[
            BronzeTableSpec(
                table_name="bronze_yield_curves",
                source_id="src_yc",
                source_table_id="src_yc:yield_curves",
                columns=b_cols,
            )
        ],
        staging_views=[
            StagingViewSpec(
                view_name="stg_yield_curves",
                source_id="src_yc",
                source_table_id="src_yc:yield_curves",
                col_lineage_mappings={c.name: c.name for c in b_cols},
                hash_recipes=[r_hk, r_hd],
                projected_fields=b_cols,
            )
        ],
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_yield_curve",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id="src_yc:yield_curves",
                        col_name="curve_id",
                        source_id="src_yc",
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[
                    SourceBindingSpec(
                        source_id="src_yc",
                        table_id="src_yc:yield_curves",
                        col_mappings={},
                        hash_recipe=r_hk,
                    )
                ],
                columns=[
                    IRField(field_id=1, name="hk_yield_curve", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="curve_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_hk,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_yield_curve"]),
            )
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name="sat_yield_curves",
                sat_kind="STANDARD",
                parent_name="hub_yield_curve",
                source_id="src_yc",
                source_table_id="src_yc:yield_curves",
                is_bitemporal=True,
                applied_dts_col="observation_date",
                attributes=[
                    DraftField(name="observation_date", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    DraftField(name="rate_2y", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
                ],
                columns=[
                    IRField(field_id=1, name="hk_yield_curve", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="observation_date", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=6, name="rate_2y", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
                ],
                hash_recipe=r_hd,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_yield_curve", "load_dts"]),
            )
        ],
    )
    rows = [{"curve_id": "UST", "observation_date": "2026-01-02", "rate_2y": "4.2500"}]
    rep = DuckDbProverAdapter().prove(ir, [], source_rows_by_table={"src_yc:yield_curves": rows})
    assert all(rep.dyn_results.values()), (rep.dyn_results, rep.counterexamples)


def test_duckdb_prover_catches_binder_error_as_counterexample() -> None:
    """Verify mismatched Link participant column vs staging returns DV-DYN-004 counterexample, not 500."""
    b_cols = [
        IRField(field_id=1, name="h_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="f_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
    ]
    r_h = HashRecipeSpec(
        target_col="hk_hold",
        recipe_kind="HUB_HK",
        bkcc_literal="GLOBAL",
        source_columns=["h_id"],
        sort_alphabetically=False,
        key_columns=["h_id"],
    )
    r_f = HashRecipeSpec(
        target_col="hk_filer",
        recipe_kind="HUB_HK",
        bkcc_literal="GLOBAL",
        source_columns=["f_id"],
        sort_alphabetically=False,
        key_columns=["f_id"],
    )
    r_lnk = HashRecipeSpec(
        target_col="hk_link",
        recipe_kind="LINK_HK",
        bkcc_literal="GLOBAL",
        source_columns=["h_id", "f_id"],
        sort_alphabetically=True,
        key_columns=["h_id", "f_id"],
    )
    ir = CertifiedVaultIR(
        project_id="proj_binder_catch",
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[
            BronzeTableSpec(
                table_name="bronze_hl",
                source_id="src_hl",
                source_table_id="src_hl:hl",
                columns=b_cols,
            )
        ],
        staging_views=[
            StagingViewSpec(
                view_name="stg_hl",
                source_id="src_hl",
                source_table_id="src_hl:hl",
                col_lineage_mappings={c.name: c.name for c in b_cols},
                hash_recipes=[r_h, r_f, r_lnk],
                projected_fields=b_cols,
            )
        ],
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_hold",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id="src_hl:hl", col_name="h_id", source_id="src_hl",
                        key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[
                    SourceBindingSpec(
                        source_id="src_hl", table_id="src_hl:hl", col_mappings={}, hash_recipe=r_h
                    )
                ],
                columns=[
                    IRField(field_id=1, name="hk_hold", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="h_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_h,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_hold"]),
            ),
            CertifiedHubSpec(
                hub_name="hub_filer",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id="src_hl:hl", col_name="f_id", source_id="src_hl",
                        key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[
                    SourceBindingSpec(
                        source_id="src_hl", table_id="src_hl:hl", col_mappings={}, hash_recipe=r_f
                    )
                ],
                columns=[
                    IRField(field_id=1, name="hk_filer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="f_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_f,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_filer"]),
            ),
        ],
        links=[
            CertifiedLinkSpec(
                link_name="lnk_hold_filer",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_hold",
                        fk_col_name="hk_missing_from_stg",
                        role_name="hold",
                        source_col_names=["h_id"],
                    ),
                    LinkParticipantSpec(
                        hub_name="hub_filer",
                        fk_col_name="hk_filer",
                        role_name="filer",
                        source_col_names=["f_id"],
                    ),
                ],
                source_bindings=[
                    SourceBindingSpec(
                        source_id="src_hl", table_id="src_hl:hl", col_mappings={}, hash_recipe=r_lnk
                    )
                ],
                columns=[
                    IRField(field_id=1, name="hk_link", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="hk_missing_from_stg", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=3, name="hk_filer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=4, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=5, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_lnk,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_link"]),
            )
        ],
    )
    rep = DuckDbProverAdapter().prove(ir, [], source_rows_by_table={"src_hl:hl": [{"h_id": "H1", "f_id": "F1"}]})
    assert rep.dyn_results.get("DV-DYN-004") is False
    assert rep.counterexamples
    assert "hk_missing_from_stg" in str(rep.counterexamples[0])
