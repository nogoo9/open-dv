"""Phase 2 Storage tests: 8-dialect participant hashing, ESAT intervals, multi-hop Bridges, and 2-pass DuckDB prover."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq
import pytest
import yaml
from sqlglot import exp

from opendv.models.ir_kernel import (
    ZERO_KEY_ERROR,
    ZERO_KEY_UNKNOWN,
    compute_participant_link_prehash,
    execute_hash_recipe,
)
from opendv.models.project import DbtSqlDialect
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
from opendv.models.vault_ir import (
    BridgeHopSpec,
    BusinessKeyPart,
    DecimalType,
    DraftField,
    LinkParticipantSpec,
    PortableLogicalType,
)
from opendv.models.vault_ir_certified import (
    BronzeTableSpec,
    CertifiedHubSpec,
    CertifiedLinkSpec,
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    HashRecipeSpec,
    IRField,
    LinkParticipantHashSpec,
    MartViewSpec,
    PhysicalLayoutHints,
    PitBridgeSpec,
    SourceBindingSpec,
    StagingViewSpec,
)
from opendv.storage.dialects import SQL_TARGET_IDS, assign_physical_aliases, get_dialect_profile
from opendv.storage.emitters.dbt import DbtEmitter
from opendv.storage.emitters.parquet import ParquetEmitter
from opendv.storage.eval_gate import TargetEvalGate
from opendv.storage.prover._real_data_prover import project_pii_token_for_type
from opendv.storage.prover.duckdb_prover import DuckDbProverAdapter, scrub_prover_exception
from opendv.storage.sql_ast_verifier import SqlAstVerifier
from opendv.storage.sql_compiler._ddl_common import col_def_sql, resolve_bridge_hop_columns
from opendv.storage.sql_compiler._sat_dml import build_sat_load_dml
from opendv.storage.sql_compiler._z3_z4_ddl import (
    build_esat_intervals_view_ddl,
    build_pit_bridge_ddl,
)
from opendv.storage.sql_compiler._z3_z4_dml import build_pit_bridge_dml
from opendv.storage.sql_compiler.compiler import SqlGlotVaultCompiler
from opendv.storage.sql_compiler.ddl_builders import build_sat_ddl
from opendv.storage.sql_compiler.dialect_hashing import (
    compile_hash_recipe_ast,
    compile_hash_recipe_sql,
)
from opendv.storage.sql_compiler.wave_migrator import WaveMigrator

_HINTS = PhysicalLayoutHints(cluster_by_cols=[])

EIGHT_DIALECTS: tuple[DbtSqlDialect, ...] = (
    "duckdb",
    "databricks",
    "snowflake",
    "postgres",
    "bigquery",
    "tsql",
    "redshift",
    "clickhouse",
)


def _participant_link_recipe() -> tuple[HashRecipeSpec, dict[str, PortableLogicalType]]:
    recipe = HashRecipeSpec(
        target_col="hk_lnk_trade",
        recipe_kind="LINK_HK",
        source_columns=["acct_id", "issuer_cik", "sub_code", "deg_tag"],
        sort_alphabetically=False,
        key_columns=["acct_id", "issuer_cik", "sub_code", "deg_tag"],
        participant_specs=[
            LinkParticipantHashSpec(
                hub_name="hub_acct",
                role_name="ACCT",
                key_tier="TIER_1_GLOBAL_STANDARD",
                bkcc_literal="GLOBAL",
                bkcc_col="tier1_ignored_bkcc",
                source_columns=["acct_id"],
                is_nullable=False,
            ),
            LinkParticipantHashSpec(
                hub_name="hub_issuer",
                role_name="ISSUER",
                key_tier="TIER_3_LOCAL_SCOPED",
                bkcc_literal="IBOR",
                bkcc_col="bkcc_src",
                source_columns=["issuer_cik", "sub_code"],
                is_nullable=True,
            ),
            LinkParticipantHashSpec(
                role_name="DEG_TAG",
                source_columns=["deg_tag"],
                is_degenerate=True,
            ),
        ],
    )
    col_types: dict[str, PortableLogicalType] = {
        "acct_id": "UTF8_STRING",
        "issuer_cik": "UTF8_STRING",
        "sub_code": "UTF8_STRING",
        "deg_tag": "UTF8_STRING",
        "bkcc_src": "UTF8_STRING",
        "tier1_ignored_bkcc": "UTF8_STRING",
    }
    return recipe, col_types


def _build_esat_ir(*, bitemporal: bool = False) -> CertifiedVaultIR:
    bronze_cols = [
        IRField(field_id=1, name="acct_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="mgr_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
    ]
    if bitemporal:
        bronze_cols.append(
            IRField(field_id=4, name="applied_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False)
        )

    hk_acct = HashRecipeSpec(
        target_col="hk_acct",
        recipe_kind="HUB_HK",
        bkcc_literal="GLOBAL",
        source_columns=["acct_id"],
        sort_alphabetically=False,
        key_columns=["acct_id"],
    )
    hk_mgr = HashRecipeSpec(
        target_col="hk_mgr",
        recipe_kind="HUB_HK",
        bkcc_literal="GLOBAL",
        source_columns=["mgr_id"],
        sort_alphabetically=False,
        key_columns=["mgr_id"],
    )
    hk_lnk = HashRecipeSpec(
        target_col="hk_lnk_acct_mgr",
        recipe_kind="LINK_HK",
        bkcc_literal="GLOBAL",
        source_columns=["acct_id", "mgr_id"],
        sort_alphabetically=False,
        key_columns=["acct_id", "mgr_id"],
    )

    esat_cols = [
        IRField(field_id=1, name="hk_lnk_acct_mgr", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
        IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
        IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
    ]
    if bitemporal:
        esat_cols.append(
            IRField(field_id=4, name="applied_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False)
        )

    return CertifiedVaultIR(
        project_id="proj_esat",
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[
            BronzeTableSpec(
                table_name="bronze_acct_mgr",
                source_id="src_core",
                source_table_id="src_core:acct_mgr",
                columns=bronze_cols,
            )
        ],
        staging_views=[
            StagingViewSpec(
                view_name="stg_acct_mgr",
                source_id="src_core",
                source_table_id="src_core:acct_mgr",
                col_lineage_mappings={c.name: c.name for c in bronze_cols},
                hash_recipes=[hk_acct, hk_mgr, hk_lnk],
                projected_fields=bronze_cols,
            )
        ],
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_acct",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id="src_core:acct_mgr",
                        col_name="acct_id",
                        source_id="src_core",
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[
                    SourceBindingSpec(
                        source_id="src_core",
                        table_id="src_core:acct_mgr",
                        col_mappings={"acct_id": "acct_id"},
                        hash_recipe=hk_acct,
                    )
                ],
                columns=[
                    IRField(field_id=1, name="hk_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="acct_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=5, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=hk_acct,
                physical_hints=_HINTS,
            ),
            CertifiedHubSpec(
                hub_name="hub_mgr",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id="src_core:acct_mgr",
                        col_name="mgr_id",
                        source_id="src_core",
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[
                    SourceBindingSpec(
                        source_id="src_core",
                        table_id="src_core:acct_mgr",
                        col_mappings={"mgr_id": "mgr_id"},
                        hash_recipe=hk_mgr,
                    )
                ],
                columns=[
                    IRField(field_id=1, name="hk_mgr", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="mgr_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=5, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=hk_mgr,
                physical_hints=_HINTS,
            ),
        ],
        links=[
            CertifiedLinkSpec(
                link_name="lnk_acct_mgr",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_acct",
                        role_name="ACCT",
                        fk_col_name="hk_acct",
                        source_col_names=["acct_id"],
                    ),
                    LinkParticipantSpec(
                        hub_name="hub_mgr",
                        role_name="MGR",
                        fk_col_name="hk_mgr",
                        source_col_names=["mgr_id"],
                    ),
                ],
                driving_hub="hub_acct",
                source_bindings=[
                    SourceBindingSpec(
                        source_id="src_core",
                        table_id="src_core:acct_mgr",
                        col_mappings={"hk_acct": "hk_acct", "hk_mgr": "hk_mgr"},
                        hash_recipe=hk_lnk,
                    )
                ],
                columns=[
                    IRField(field_id=1, name="hk_lnk_acct_mgr", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="hk_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=3, name="hk_mgr", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=4, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=5, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=hk_lnk,
                physical_hints=_HINTS,
            )
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name="esat_acct_mgr",
                zone_id="Z2",
                sat_kind="ESAT",
                parent_name="lnk_acct_mgr",
                source_id="src_core",
                source_table_id="src_core:acct_mgr",
                is_bitemporal=bitemporal,
                applied_dts_col="applied_dts" if bitemporal else None,
                driving_keys=["hk_acct"],
                columns=esat_cols,
                physical_hints=_HINTS,
            )
        ],
        information_marts=[
            MartViewSpec(
                view_name="vw_esat_acct_mgr_intervals",
                zone_id="Z3",
                view_kind="ESAT_INTERVALS",
                root_entity="esat_acct_mgr",
                joined_entities=["lnk_acct_mgr"],
                columns=[
                    IRField(field_id=1, name="hk_lnk_acct_mgr", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="hk_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=3, name="effective_from", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="effective_to", logical_type="TIMESTAMP_UTC_US", nullable=True, is_pii=False),
                    IRField(field_id=5, name="is_current", logical_type="BOOLEAN", nullable=False, is_pii=False),
                ],
                select_exprs={},
            )
        ],
    )


def _build_multihop_bridge_ir(*, two_hops: bool = True) -> CertifiedVaultIR:
    b_ab_cols = [
        IRField(field_id=1, name="a_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="b_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
    ]
    b_bc_cols = [
        IRField(field_id=1, name="b_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=2, name="c_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
    ]
    hk_a = HashRecipeSpec(
        target_col="hk_a", recipe_kind="HUB_HK", bkcc_literal="GLOBAL", source_columns=["a_id"], sort_alphabetically=False, key_columns=["a_id"]
    )
    hk_b = HashRecipeSpec(
        target_col="hk_b", recipe_kind="HUB_HK", bkcc_literal="GLOBAL", source_columns=["b_id"], sort_alphabetically=False, key_columns=["b_id"]
    )
    hk_c = HashRecipeSpec(
        target_col="hk_c", recipe_kind="HUB_HK", bkcc_literal="GLOBAL", source_columns=["c_id"], sort_alphabetically=False, key_columns=["c_id"]
    )
    hk_ab = HashRecipeSpec(
        target_col="hk_lnk_ab", recipe_kind="LINK_HK", bkcc_literal="GLOBAL", source_columns=["a_id", "b_id"], sort_alphabetically=False, key_columns=["a_id", "b_id"]
    )
    hk_bc = HashRecipeSpec(
        target_col="hk_lnk_bc", recipe_kind="LINK_HK", bkcc_literal="GLOBAL", source_columns=["b_id", "c_id"], sort_alphabetically=False, key_columns=["b_id", "c_id"]
    )

    def _hub(name: str, hk: str, bk: str, tid: str, recipe: HashRecipeSpec) -> CertifiedHubSpec:
        return CertifiedHubSpec(
            hub_name=name,
            key_tier="TIER_1_GLOBAL_STANDARD",
            business_keys=[
                BusinessKeyPart(table_id=tid, col_name=bk, source_id="src_b", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")
            ],
            source_bindings=[SourceBindingSpec(source_id="src_b", table_id=tid, col_mappings={bk: bk}, hash_recipe=recipe)],
            columns=[
                IRField(field_id=1, name=hk, logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                IRField(field_id=3, name=bk, logical_type="UTF8_STRING", nullable=False, is_pii=False),
                IRField(field_id=4, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                IRField(field_id=5, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
            ],
            hash_recipe=recipe,
            physical_hints=_HINTS,
        )

    hops = [BridgeHopSpec(link_name="lnk_ab", from_fk_col="hk_a", to_fk_col="hk_b")]
    targets = ["lnk_ab"]
    trav = ["hk_b"]
    if two_hops:
        hops.append(BridgeHopSpec(link_name="lnk_bc", from_fk_col="hk_b", to_fk_col="hk_c"))
        targets.append("lnk_bc")
        trav.append("hk_c")

    return CertifiedVaultIR(
        project_id="proj_brg",
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[
            BronzeTableSpec(table_name="bronze_ab", source_id="src_b", source_table_id="src_b:ab", columns=b_ab_cols),
            BronzeTableSpec(table_name="bronze_bc", source_id="src_b", source_table_id="src_b:bc", columns=b_bc_cols),
        ],
        staging_views=[
            StagingViewSpec(
                view_name="stg_ab",
                source_id="src_b",
                source_table_id="src_b:ab",
                col_lineage_mappings={"a_id": "a_id", "b_id": "b_id"},
                hash_recipes=[hk_a, hk_b, hk_ab],
                projected_fields=b_ab_cols,
            ),
            StagingViewSpec(
                view_name="stg_bc",
                source_id="src_b",
                source_table_id="src_b:bc",
                col_lineage_mappings={"b_id": "b_id", "c_id": "c_id"},
                hash_recipes=[hk_b, hk_c, hk_bc],
                projected_fields=b_bc_cols,
            ),
        ],
        hubs=[
            _hub("hub_a", "hk_a", "a_id", "src_b:ab", hk_a),
            _hub("hub_b", "hk_b", "b_id", "src_b:ab", hk_b),
            _hub("hub_c", "hk_c", "c_id", "src_b:bc", hk_c),
        ],
        links=[
            CertifiedLinkSpec(
                link_name="lnk_ab",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_a",
                        role_name="A",
                        fk_col_name="hk_a",
                        source_col_names=["a_id"],
                    ),
                    LinkParticipantSpec(
                        hub_name="hub_b",
                        role_name="B",
                        fk_col_name="hk_b",
                        source_col_names=["b_id"],
                    ),
                ],
                source_bindings=[SourceBindingSpec(source_id="src_b", table_id="src_b:ab", col_mappings={"hk_a": "hk_a", "hk_b": "hk_b"}, hash_recipe=hk_ab)],
                columns=[
                    IRField(field_id=1, name="hk_lnk_ab", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="hk_a", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=3, name="hk_b", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=4, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=5, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=hk_ab,
                physical_hints=_HINTS,
            ),
            CertifiedLinkSpec(
                link_name="lnk_bc",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_b",
                        role_name="B",
                        fk_col_name="hk_b",
                        source_col_names=["b_id"],
                    ),
                    LinkParticipantSpec(
                        hub_name="hub_c",
                        role_name="C",
                        fk_col_name="hk_c",
                        source_col_names=["c_id"],
                    ),
                ],
                source_bindings=[SourceBindingSpec(source_id="src_b", table_id="src_b:bc", col_mappings={"hk_b": "hk_b", "hk_c": "hk_c"}, hash_recipe=hk_bc)],
                columns=[
                    IRField(field_id=1, name="hk_lnk_bc", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="hk_b", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=3, name="hk_c", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=4, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=5, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=hk_bc,
                physical_hints=_HINTS,
            ),
        ],
        pit_bridges=[
            PitBridgeSpec(
                object_name="brg_a_to_c",
                object_kind="BRIDGE",
                root_hub="hub_a",
                target_satellites_or_links=targets,
                grain_cols=["hk_a"],
                hops=hops,
                traversed_hk_cols=trav,
            )
        ],
    )


def test_participant_link_8dialect_ast_and_duckdb_parity() -> None:
    """Participant LINK_HK parity across 8 dialects, DuckDB execution, TargetEvalGate, & DV-AST-003."""
    recipe, col_types = _participant_link_recipe()
    verifier = SqlAstVerifier()

    for dialect in EIGHT_DIALECTS:
        ast_node = compile_hash_recipe_ast(recipe, dialect, col_types=col_types)
        assert not any(isinstance(sub, exp.Command) for sub in ast_node.walk())
        sql_expr = compile_hash_recipe_sql(recipe, dialect, col_types=col_types)
        diags = verifier.verify_sql(f"SELECT {sql_expr} AS hk_lnk_trade FROM src_t", dialect)
        assert diags == [], f"{dialect} failed AST verification: {diags}"

    # Pure-Python prehash semantics: TIER_1 bkcc_col suppression, degenerate case/NULL, partial-NULL -1 vs -2
    pre_mixed, _ = compute_participant_link_prehash(
        recipe,
        {
            "acct_id": "aBc-1",
            "issuer_cik": "xYz",
            "sub_code": "sUb",
            "deg_tag": "MixedCase",
            "bkcc_src": "eu_cust",
            "tier1_ignored_bkcc": "MUST_BE_IGNORED",
        },
        column_types=col_types,
    )
    assert pre_mixed == "6:GLOBAL||5:ABC-1||7:EU_CUST||3:XYZ||3:SUB||9:MixedCase"
    assert "MUST_BE_IGNORED" not in pre_mixed

    pre_deg_null, _ = compute_participant_link_prehash(
        recipe,
        {"acct_id": "A1", "issuer_cik": "I1", "sub_code": "S1", "deg_tag": None, "bkcc_src": "ibor"},
        column_types=col_types,
    )
    assert pre_deg_null.endswith("||7:UNKNOWN")

    pre_partial_unk, _ = compute_participant_link_prehash(
        recipe,
        {"acct_id": None, "issuer_cik": "I1", "sub_code": "S1", "deg_tag": "D1", "bkcc_src": "ibor"},
        column_types=col_types,
    )
    pre_partial_err, _ = compute_participant_link_prehash(
        recipe,
        {"acct_id": "A1", "issuer_cik": None, "sub_code": None, "deg_tag": "D1", "bkcc_src": "ibor"},
        column_types=col_types,
    )
    assert "-1:^^||-1:^^" in pre_partial_unk
    assert "-2:^^||-2:^^||-2:^^" in pre_partial_err

    # Hub-only blank short-circuit (even when degenerate key is non-null!)
    row_hub_blank_unk = {
        "acct_id": None, "issuer_cik": None, "sub_code": None, "deg_tag": "KeepDeg", "bkcc_src": "ibor"
    }
    row_hub_blank_err = {
        "acct_id": "-2", "issuer_cik": None, "sub_code": None, "deg_tag": "KeepDeg", "bkcc_src": "ibor"
    }
    assert execute_hash_recipe(recipe, row_hub_blank_unk, column_types=col_types) == ZERO_KEY_UNKNOWN
    assert execute_hash_recipe(recipe, row_hub_blank_err, column_types=col_types) == ZERO_KEY_ERROR

    # Live DuckDB parity across all edge rows
    test_rows: list[dict[str, Any]] = [
        {"acct_id": "aBc-1", "issuer_cik": "xYz", "sub_code": "sUb", "deg_tag": "MixedCase", "bkcc_src": "eu_cust", "tier1_ignored_bkcc": "IGNORED"},
        {"acct_id": "A1", "issuer_cik": "I1", "sub_code": "S1", "deg_tag": None, "bkcc_src": None, "tier1_ignored_bkcc": "X"},
        {"acct_id": None, "issuer_cik": "I1", "sub_code": "S1", "deg_tag": "D1", "bkcc_src": "ibor", "tier1_ignored_bkcc": None},
        {"acct_id": "A1", "issuer_cik": None, "sub_code": None, "deg_tag": "D1", "bkcc_src": "ibor", "tier1_ignored_bkcc": None},
        {"acct_id": "-1", "issuer_cik": "I1", "sub_code": "S1", "deg_tag": "D1", "bkcc_src": "ibor", "tier1_ignored_bkcc": None},
        {"acct_id": "-2", "issuer_cik": "I1", "sub_code": "S1", "deg_tag": "D1", "bkcc_src": "ibor", "tier1_ignored_bkcc": None},
        {"acct_id": None, "issuer_cik": None, "sub_code": None, "deg_tag": "KeepDeg", "bkcc_src": "ibor", "tier1_ignored_bkcc": None},
        {"acct_id": "-2", "issuer_cik": None, "sub_code": None, "deg_tag": "KeepDeg", "bkcc_src": "ibor", "tier1_ignored_bkcc": None},
    ]
    conn = duckdb.connect(":memory:", config={"enable_external_access": False})
    try:
        conn.execute(
            "CREATE TABLE src_t (acct_id VARCHAR, issuer_cik VARCHAR, sub_code VARCHAR, "
            "deg_tag VARCHAR, bkcc_src VARCHAR, tier1_ignored_bkcc VARCHAR)"
        )
        for r in test_rows:
            conn.execute(
                "INSERT INTO src_t VALUES (?, ?, ?, ?, ?, ?)",
                [r["acct_id"], r["issuer_cik"], r["sub_code"], r["deg_tag"], r["bkcc_src"], r["tier1_ignored_bkcc"]],
            )
        duck_expr = compile_hash_recipe_sql(recipe, "duckdb", col_types=col_types)
        got = conn.execute(f"SELECT LOWER(HEX({duck_expr})) FROM src_t").fetchall()
        for idx, (sql_hex,) in enumerate(got):
            py_hex = execute_hash_recipe(recipe, test_rows[idx], column_types=col_types)
            assert str(sql_hex).lower() == py_hex
        # Partial-NULL (-1:^^) and partial-nullable-NULL (-2:^^) produce distinct non-zero hashes
        assert got[2][0].lower() != got[3][0].lower()
        assert got[4][0].lower() != got[5][0].lower()
    finally:
        conn.close()

    # TargetEvalGate built-in cross-dialect vector suite
    gate = TargetEvalGate()
    assert gate.verify_cross_dialect_vectors(raise_on_error=True) == []

    # DV-AST-003 accepts both '-1:^^' and '-2:^^' as valid null sentinel tokens
    sql_only_minus1 = (
        "SELECT UNHEX(SHA256('6:GLOBAL||' || CASE WHEN col IS NULL THEN '-1:^^' "
        "ELSE CAST(OCTET_LENGTH(col) AS VARCHAR) || ':' || col END)) AS hk_foo FROM tbl"
    )
    sql_only_minus2 = (
        "SELECT UNHEX(SHA256('6:GLOBAL||' || CASE WHEN col IS NULL THEN '-2:^^' "
        "ELSE CAST(OCTET_LENGTH(col) AS VARCHAR) || ':' || col END)) AS hk_foo FROM tbl"
    )
    assert verifier.verify_sql(sql_only_minus1, "duckdb_local") == []
    assert verifier.verify_sql(sql_only_minus2, "duckdb_local") == []


def test_esat_aba_reactivation_and_bitemporal_intervals_live_duckdb() -> None:
    """Z2 esat_* ABA reactivation + Z3 vw_*_intervals applied_dts ordering & 8-dialect AST."""
    # 1. ABA reactivation across 3 incremental batches
    ir_aba = _build_esat_ir(bitemporal=False)
    bundle = SqlGlotVaultCompiler("duckdb_local").compile(ir_aba)

    conn = duckdb.connect(":memory:", config={"enable_external_access": False})
    try:
        for stmt in bundle.ddl_statements:
            conn.execute(stmt)

        batches = [
            ("A1", "M1", datetime(2026, 1, 1, 0, 0, tzinfo=UTC)),
            ("A1", "M2", datetime(2026, 1, 2, 0, 0, tzinfo=UTC)),
            ("A1", "M1", datetime(2026, 1, 3, 0, 0, tzinfo=UTC)),
        ]
        for acct, mgr, ts in batches:
            conn.execute("DELETE FROM bronze.bronze_acct_mgr")
            conn.execute("INSERT INTO bronze.bronze_acct_mgr VALUES (?, ?, ?)", [acct, mgr, ts])
            for stmt in bundle.dml_statements:
                conn.execute(stmt)

        esat_rows = conn.execute(
            "SELECT LOWER(HEX(hk_lnk_acct_mgr)), "
            "STRFTIME(load_dts AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ') "
            "FROM raw_vault.esat_acct_mgr "
            "WHERE hk_lnk_acct_mgr <> UNHEX(REPEAT('00', 32)) "
            "ORDER BY load_dts ASC"
        ).fetchall()
        assert len(esat_rows) == 3
        assert esat_rows[0][0] == esat_rows[2][0]  # ABA: ROW 1 -> ROW 2 -> ROW 1
        assert esat_rows[0][0] != esat_rows[1][0]

        iv_rows = conn.execute(
            "SELECT LOWER(HEX(hk_lnk_acct_mgr)), "
            "STRFTIME(effective_from AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ'), "
            "STRFTIME(effective_to AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ'), "
            "is_current "
            "FROM business_vault.vw_esat_acct_mgr_intervals "
            "ORDER BY effective_from ASC"
        ).fetchall()
        assert len(iv_rows) == 3
        assert iv_rows[0][0] == iv_rows[2][0]
        assert iv_rows[0][2] == iv_rows[1][1]
        assert iv_rows[1][2] == iv_rows[2][1]
        assert iv_rows[2][2] is None
        assert [r[3] for r in iv_rows] == [False, False, True]
    finally:
        conn.close()

    # 2. Bitemporal applied_dts ordering (later load_dts carrying earlier applied_dts) without hk_lnk tie-break
    ir_bi = _build_esat_ir(bitemporal=True)
    bundle_bi = SqlGlotVaultCompiler("duckdb_local").compile(ir_bi)
    conn_bi = duckdb.connect(":memory:", config={"enable_external_access": False})
    try:
        for stmt in bundle_bi.ddl_statements:
            conn_bi.execute(stmt)
        # Insert initial state at applied=Jan 10 (loaded Jan 10), then retroactive correction at applied=Jan 05 (loaded Jan 20)
        bi_rows = [
            ("A1", "M2", datetime(2026, 1, 10, 0, 0, tzinfo=UTC), datetime(2026, 1, 10, 0, 0, tzinfo=UTC)),
            ("A1", "M1", datetime(2026, 1, 20, 0, 0, tzinfo=UTC), datetime(2026, 1, 5, 0, 0, tzinfo=UTC)),
        ]
        for acct, mgr, ldts, adts in bi_rows:
            conn_bi.execute("DELETE FROM bronze.bronze_acct_mgr")
            conn_bi.execute("INSERT INTO bronze.bronze_acct_mgr VALUES (?, ?, ?, ?)", [acct, mgr, ldts, adts])
            for stmt in bundle_bi.dml_statements:
                conn_bi.execute(stmt)

        iv_bi = conn_bi.execute(
            "SELECT LOWER(HEX(hk_lnk_acct_mgr)), "
            "STRFTIME(effective_from AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ'), "
            "STRFTIME(effective_to AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ'), "
            "is_current "
            "FROM business_vault.vw_esat_acct_mgr_intervals "
            "ORDER BY effective_from ASC"
        ).fetchall()
        assert len(iv_bi) == 2
        # Retroactive Jan 05 row precedes Jan 10 row; neither is zero-key
        assert iv_bi[0][1] == "2026-01-05T00:00:00Z"
        assert iv_bi[0][2] == "2026-01-10T00:00:00Z"
        assert iv_bi[0][3] is False
        assert iv_bi[1][2] is None
        assert iv_bi[1][3] is True
    finally:
        conn_bi.close()

    # 3. 8-dialect AST parse cleanliness and absence of hk_lnk tie-breaking in ORDER BY
    verifier = SqlAstVerifier()
    mart = ir_bi.information_marts[0]
    for tid in SQL_TARGET_IDS:
        prof = get_dialect_profile(tid)
        aliases = assign_physical_aliases(ir_bi, tid)
        ddls = build_esat_intervals_view_ddl(mart, ir_bi, prof, aliases)
        assert ddls
        joined = ";\n".join(ddls)
        assert verifier.verify_sql(joined, tid, ir=ir_bi) == []
        assert "ORDER BY COALESCE(" in joined
        assert "hk_lnk_acct_mgr ASC" not in joined


def test_multihop_bridge_ddl_dml_parquet_wave_migrator_and_ast_rules(tmp_path: Path) -> None:
    """Multi-hop Bridge LEFT JOIN, live DuckDB, Parquet schema, WaveMigrator, & AST Z3 rules."""
    ir_w1 = _build_multihop_bridge_ir(two_hops=True)
    pb = ir_w1.pit_bridges[0]
    src_root_hk, brg_root_hk, hop_specs = resolve_bridge_hop_columns(pb, ir_w1, 255)
    assert src_root_hk == "hk_a"
    assert brg_root_hk == "hk_a"
    assert len(hop_specs) == 2
    assert hop_specs[0].join_parent_idx == -1
    assert hop_specs[0].from_fk_col == "hk_a"
    assert hop_specs[0].to_fk_col == "hk_b"
    assert hop_specs[1].join_parent_idx == 0
    assert hop_specs[1].from_fk_col == "hk_b"
    assert hop_specs[1].to_fk_col == "hk_c"

    bundle = SqlGlotVaultCompiler("duckdb_local").compile(ir_w1)
    conn = duckdb.connect(":memory:", config={"enable_external_access": False})
    try:
        for stmt in bundle.ddl_statements:
            conn.execute(stmt)
        # A1 -> B1 -> C1 (full 2-hop), A2 -> B2 (hop2 missing -> LEFT JOIN preserves with 00..00)
        conn.execute("INSERT INTO bronze.bronze_ab VALUES ('A1', 'B1'), ('A2', 'B2')")
        conn.execute("INSERT INTO bronze.bronze_bc VALUES ('B1', 'C1')")
        for stmt in bundle.dml_statements:
            conn.execute(stmt)

        rows = conn.execute(
            "SELECT LOWER(HEX(hk_a)), LOWER(HEX(hk_lnk_ab)), LOWER(HEX(hk_b)), "
            "LOWER(HEX(hk_lnk_bc)), LOWER(HEX(hk_c)) "
            "FROM business_vault.brg_a_to_c ORDER BY hk_a"
        ).fetchall()
        assert len(rows) == 2
        by_zero_hop2 = {r[4]: r for r in rows}
        assert ZERO_KEY_UNKNOWN in by_zero_hop2
        unmatched = by_zero_hop2[ZERO_KEY_UNKNOWN]
        assert unmatched[1] != ZERO_KEY_UNKNOWN  # hop 1 matched
        assert unmatched[3] == ZERO_KEY_UNKNOWN  # hop 2 unmatched -> sentinel
        matched = next(r for r in rows if r[4] != ZERO_KEY_UNKNOWN)
        assert matched[1] != ZERO_KEY_UNKNOWN
        assert matched[3] != ZERO_KEY_UNKNOWN
    finally:
        conn.close()

    # ParquetEmitter Z3 brg_* schema emission
    pq_out = tmp_path / "pq_bundle"
    pq_bundle = ParquetEmitter().emit(ir_w1, pq_out)
    brg_pq = pq_out / "z3_derived/brg_a_to_c/latest/part-00000.zstd.parquet"
    assert brg_pq.is_file()
    assert "z3_derived/brg_a_to_c/latest/part-00000.zstd.parquet" in pq_bundle.files
    pq_schema = pq.read_schema(brg_pq)
    assert pq_schema.names == ["hk_a", "hk_lnk_ab", "hk_b", "hk_lnk_bc", "hk_c", "load_dts"]

    # WaveMigrator: Z3 DROP_AND_CREATE on additive evolution vs steady-state TRUNCATE + INSERT
    ir_one_hop = _build_multihop_bridge_ir(two_hops=False)
    ir_two_hop = ir_w1.model_copy(update={"wave_number": 2})
    migrator = WaveMigrator()
    mig_evolved = migrator.compile_migration(ir_one_hop, ir_two_hop, "sqlserver_fabric")
    mig_evolved_sql = "\n".join(mig_evolved)
    assert "DROP TABLE IF EXISTS business_vault.brg_a_to_c" in mig_evolved_sql
    assert "CREATE TABLE business_vault.brg_a_to_c" in mig_evolved_sql

    # Unchanged Bridge schema emits 0 DROP/CREATE for Bridge in migration; steady-state uses TRUNCATE + INSERT
    mig_same = migrator.compile_migration(ir_w1, ir_two_hop, "sqlserver_fabric")
    assert not any("brg_a_to_c" in s for s in mig_same)

    steady_dml = build_pit_bridge_dml(
        pb, ir_w1, ir_w1.seed_records, get_dialect_profile("duckdb_local"), assign_physical_aliases(ir_w1, "duckdb_local")
    )
    assert steady_dml[0].startswith("TRUNCATE TABLE business_vault.brg_a_to_c")
    assert steady_dml[1].startswith("INSERT INTO business_vault.brg_a_to_c")

    # _ast_stmt_rules: block TRUNCATE/DROP on Z3 sal_* / esat_sal_*, allow pit_* / brg_*
    verifier = SqlAstVerifier()
    assert len(verifier.verify_sql("TRUNCATE TABLE business_vault.sal_issuer", "duckdb_local")) >= 1
    assert len(verifier.verify_sql("DROP TABLE business_vault.esat_sal_issuer", "duckdb_local")) >= 1
    assert verifier.verify_sql("TRUNCATE TABLE business_vault.pit_issuer", "duckdb_local") == []
    assert verifier.verify_sql("TRUNCATE TABLE business_vault.brg_a_to_c", "duckdb_local") == []
    assert verifier.verify_sql("DROP TABLE business_vault.brg_a_to_c", "duckdb_local") == []


def test_tsql_mas_pk_clickhouse_settings_and_dbt_z2_z3_models() -> None:
    """T-SQL MAS CDK PK collation, ClickHouse join_use_nulls=1, & dbt Z2/Z3 emission."""
    # 1. T-SQL MAS CDK PK uses VARCHAR(255) COLLATE ... NOT NULL
    cdk_field = IRField(field_id=5, name="ballot_seq", logical_type="UTF8_STRING", nullable=False, is_pii=False)
    col_sql = col_def_sql(cdk_field, "sqlserver_fabric", "tsql", is_key_col=True)
    assert col_sql.startswith("ballot_seq VARCHAR(255) COLLATE ")
    assert col_sql.endswith(" NOT NULL")

    mas_sat = CertifiedSatelliteSpec(
        sat_name="mas_ballot",
        zone_id="Z2",
        sat_kind="MAS",
        parent_name="hub_acct",
        source_id="src_core",
        source_table_id="src_core:ballot",
        child_dependent_keys=["ballot_seq"],
        columns=[
            IRField(field_id=1, name="hk_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
            IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
            IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
            IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
            cdk_field,
        ],
        physical_hints=_HINTS,
    )
    mas_ddl = build_sat_ddl(mas_sat, get_dialect_profile("sqlserver_fabric"), {"mas_ballot": "mas_ballot"})
    assert "ballot_seq VARCHAR(255) COLLATE " in mas_ddl
    assert "PRIMARY KEY (hk_acct, load_dts, ballot_seq)" in mas_ddl

    # 2. ClickHouse statement-level SETTINGS join_use_nulls = 1
    ir_base = _build_esat_ir(bitemporal=False)
    ch_prof = get_dialect_profile("clickhouse")
    ch_aliases = assign_physical_aliases(ir_base, "clickhouse")
    std_sat = CertifiedSatelliteSpec(
        sat_name="sat_acct_core",
        zone_id="Z2",
        sat_kind="STANDARD",
        parent_name="hub_acct",
        source_id="src_core",
        source_table_id="src_core:acct_mgr",
        columns=[
            IRField(field_id=1, name="hk_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
            IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
            IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
            IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
            IRField(field_id=5, name="mgr_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        ],
        hash_recipe=HashRecipeSpec(
            target_col="hash_diff", recipe_kind="SAT_HASH_DIFF", source_columns=["mgr_id"], sort_alphabetically=True
        ),
        physical_hints=_HINTS,
    )
    ch_sat_dml = build_sat_load_dml(
        std_sat,
        ir_base.staging_views[0],
        ir_base,
        ch_prof,
        ch_aliases,
        {("stg_acct_mgr", "sat_acct_core"): "hash_diff"},
    )
    assert "SETTINGS join_use_nulls = 1" in ch_sat_dml
    assert SqlAstVerifier().verify_sql(ch_sat_dml, "clickhouse") == []

    ir_brg = _build_multihop_bridge_ir(two_hops=True)
    ch_brg_dml = build_pit_bridge_dml(
        ir_brg.pit_bridges[0], ir_brg, ir_brg.seed_records, ch_prof, assign_physical_aliases(ir_brg, "clickhouse")
    )
    assert "SETTINGS join_use_nulls = 1" in ch_brg_dml[1]
    assert SqlAstVerifier().verify_sql(ch_brg_dml[1], "clickhouse") == []

    # 3. dbt.py: Z2 esat_* native incremental, Z3 models materialized='view', Z3 schema.yml completeness
    z3_esat_sal = CertifiedSatelliteSpec(
        sat_name="esat_sal_acct",
        zone_id="Z3",
        sat_kind="ESAT",
        parent_name="sal_acct",
        source_id="src_core",
        source_table_id="src_core:acct_mgr",
        driving_keys=["hk_acct"],
        columns=[
            IRField(field_id=1, name="hk_sal_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
            IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
            IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        ],
        physical_hints=_HINTS,
    )
    z3_sal = CertifiedLinkSpec(
        link_name="sal_acct",
        zone_id="Z3",
        link_kind="SAME_AS",
        participants=[
            LinkParticipantSpec(
                hub_name="hub_acct",
                role_name="CANON",
                fk_col_name="hk_acct",
                source_col_names=["acct_id"],
            ),
            LinkParticipantSpec(
                hub_name="hub_mgr",
                role_name="DUP",
                fk_col_name="hk_mgr",
                source_col_names=["mgr_id"],
            ),
        ],
        source_bindings=[
            SourceBindingSpec(
                source_id="src_core",
                table_id="src_core:acct_mgr",
                col_mappings={"hk_acct": "hk_acct", "hk_mgr": "hk_mgr"},
            )
        ],
        columns=[
            IRField(field_id=1, name="hk_sal_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
            IRField(field_id=2, name="hk_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
            IRField(field_id=3, name="hk_mgr", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
            IRField(field_id=4, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
            IRField(field_id=5, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        ],
        physical_hints=_HINTS,
    )
    z3_brg = PitBridgeSpec(
        object_name="brg_acct_mgr",
        object_kind="BRIDGE",
        root_hub="hub_acct",
        target_satellites_or_links=["lnk_acct_mgr"],
        grain_cols=["hk_acct"],
        hops=[BridgeHopSpec(link_name="lnk_acct_mgr", from_fk_col="hk_acct", to_fk_col="hk_mgr")],
        traversed_hk_cols=["hk_mgr"],
    )
    ir_dbt = ir_base.model_copy(
        update={
            "links": [*ir_base.links, z3_sal],
            "satellites": [*ir_base.satellites, z3_esat_sal],
            "pit_bridges": [z3_brg],
        }
    )
    dbt_bundle = DbtEmitter().emit(ir_dbt, dbt_sql_dialect="duckdb")
    z2_esat_sql = dbt_bundle.files["models/Z2_raw_vault/esat_acct_mgr.sql"]
    assert "{{ config(materialized='incremental') }}" in z2_esat_sql
    assert "{% if is_incremental() %}" in z2_esat_sql

    for z3_sql_key in (
        "models/Z3_business_vault/esat_sal_acct.sql",
        "models/Z3_business_vault/brg_acct_mgr.sql",
        "models/Z3_business_vault/vw_esat_acct_mgr_intervals.sql",
    ):
        assert dbt_bundle.files[z3_sql_key].startswith("{{ config(materialized='view') }}")

    z3_schema = yaml.safe_load(dbt_bundle.files["models/Z3_business_vault/schema.yml"])
    z3_names = {m["name"] for m in z3_schema["models"]}
    assert z3_names == {"sal_acct", "esat_sal_acct", "brg_acct_mgr", "vw_esat_acct_mgr_intervals"}


def _build_two_source_ir_for_prover() -> tuple[CertifiedVaultIR, list[SourceProfile]]:
    t_hub_cols = [
        IRField(field_id=1, name="acct_id", logical_type="UTF8_STRING", nullable=False, is_pii=True),
        IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
    ]
    t_sat_cols = [
        IRField(field_id=1, name="acct_id", logical_type="UTF8_STRING", nullable=False, is_pii=True),
        IRField(field_id=2, name="seq_no", logical_type="INT32", nullable=False, is_pii=False),
        IRField(field_id=3, name="attr_val", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=4, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
    ]
    hk_acct = HashRecipeSpec(
        target_col="hk_acct", recipe_kind="HUB_HK", bkcc_literal="GLOBAL", source_columns=["acct_id"], sort_alphabetically=False, key_columns=["acct_id"]
    )
    hd_sat = HashRecipeSpec(
        target_col="hash_diff", recipe_kind="SAT_HASH_DIFF", source_columns=["attr_val"], sort_alphabetically=True
    )
    ir = CertifiedVaultIR(
        project_id="proj_prover",
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[
            BronzeTableSpec(table_name="bronze_accts", source_id="src_a", source_table_id="src_a:accts", columns=t_hub_cols),
            BronzeTableSpec(table_name="bronze_attrs", source_id="src_b", source_table_id="src_b:attrs", columns=t_sat_cols),
        ],
        staging_views=[
            StagingViewSpec(
                view_name="stg_accts",
                source_id="src_a",
                source_table_id="src_a:accts",
                col_lineage_mappings={c.name: c.name for c in t_hub_cols},
                hash_recipes=[hk_acct],
                projected_fields=t_hub_cols,
            ),
            StagingViewSpec(
                view_name="stg_attrs",
                source_id="src_b",
                source_table_id="src_b:attrs",
                col_lineage_mappings={c.name: c.name for c in t_sat_cols},
                hash_recipes=[hk_acct, hd_sat],
                projected_fields=t_sat_cols,
            ),
        ],
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_acct",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(table_id="src_a:accts", col_name="acct_id", source_id="src_a", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")
                ],
                source_bindings=[
                    SourceBindingSpec(source_id="src_a", table_id="src_a:accts", col_mappings={"acct_id": "acct_id"}, hash_recipe=hk_acct)
                ],
                columns=[
                    IRField(field_id=1, name="hk_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="acct_id", logical_type="UTF8_STRING", nullable=False, is_pii=True),
                    IRField(field_id=4, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=5, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=hk_acct,
                physical_hints=_HINTS,
            )
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name="sat_acct_attrs",
                zone_id="Z2",
                sat_kind="STANDARD",
                parent_name="hub_acct",
                source_id="src_b",
                source_table_id="src_b:attrs",
                attributes=[
                    DraftField(name="seq_no", logical_type="INT32", nullable=False, is_pii=False),
                    DraftField(name="attr_val", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                columns=[
                    IRField(field_id=1, name="hk_acct", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=5, name="attr_val", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                hash_recipe=hd_sat,
                physical_hints=_HINTS,
            )
        ],
    )
    profs = [
        SourceProfile(
            source_id="src_a",
            schema_fingerprint="fp_a",
            table_count=1,
            tables=[
                TableProfile(
                    table_id="src_a:accts",
                    table_name="accts",
                    row_count_est=1,
                    columns=[
                        ColumnProfile(col_name="acct_id", sql_type="VARCHAR", nullable=False, is_pk=True, null_ratio=0.0, distinct_count_est=1, pii_tags=["EMAIL"], sample_values=["<PII:0123456789abcdef>"]),
                    ],
                )
            ],
        ),
        SourceProfile(
            source_id="src_b",
            schema_fingerprint="fp_b",
            table_count=1,
            tables=[
                TableProfile(
                    table_id="src_b:attrs",
                    table_name="attrs",
                    row_count_est=2,
                    columns=[
                        ColumnProfile(col_name="acct_id", sql_type="VARCHAR", nullable=False, is_pk=False, null_ratio=0.0, distinct_count_est=1, pii_tags=["EMAIL"], sample_values=["<PII:0123456789abcdef>"]),
                        ColumnProfile(col_name="seq_no", sql_type="INTEGER", nullable=False, is_pk=False, null_ratio=0.0, distinct_count_est=2, pii_tags=[], sample_values=["1", "2"]),
                        ColumnProfile(col_name="attr_val", sql_type="VARCHAR", nullable=False, is_pk=False, null_ratio=0.0, distinct_count_est=2, pii_tags=[], sample_values=["X", "Y"]),
                    ],
                )
            ],
        ),
    ]
    return ir, profs


def test_two_pass_prover_pii_projection_partial_sample_and_remediation() -> None:
    """Two-pass DuckDbProverAdapter: PII projection, SKIPPED_PARTIAL_SAMPLE, opt-out, DV-DYN-004 hint, & scrub."""
    # 1. Type-preserving PII projector & join equality
    tok_a = "<PII:0123456789abcdef>"
    tok_b = "<PII:fedcba9876543210>"
    i1 = project_pii_token_for_type(tok_a, "INT64")
    i2 = project_pii_token_for_type(tok_a, "INT64")
    assert isinstance(i1, int) and i1 == i2
    assert i1 != project_pii_token_for_type(tok_b, "INT64")
    d1 = project_pii_token_for_type(tok_a, "DATE32")
    assert isinstance(d1, date) and d1 == project_pii_token_for_type(tok_a, "DATE32")
    dec1 = project_pii_token_for_type(tok_a, DecimalType(precision=18, scale=4))
    assert isinstance(dec1, Decimal) and dec1 == project_pii_token_for_type(tok_a, DecimalType(precision=18, scale=4))

    prover = DuckDbProverAdapter()
    ir, profs = _build_two_source_ir_for_prover()
    ts = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

    # 2. real_row_proving=False opt-out -> Pass 1 passes, Pass 2 DISABLED_BY_USER
    rep_off = prover.prove(ir, profs, real_row_proving=False)
    assert all(rep_off.schema_proof_results.values())
    assert rep_off.real_data_proof_results["DV-DYN-ALL"].status == "DISABLED_BY_USER"

    # 3. Cross-source partial parent sample -> DV-DYN-006 SKIPPED_PARTIAL_SAMPLE
    rows_clean = {
        "src_a:accts": [{"acct_id": tok_a, "load_dts": ts}],
        "src_b:attrs": [{"acct_id": tok_a, "seq_no": 1, "attr_val": "X", "load_dts": ts}],
    }
    rep_partial = prover.prove(
        ir,
        profs,
        source_rows_by_table=rows_clean,
        table_sample_flags={"src_a:accts": False, "src_b:attrs": True},
    )
    assert rep_partial.real_data_proof_results["DV-DYN-006"].status == "SKIPPED_PARTIAL_SAMPLE"
    assert rep_partial.real_data_proof_results["DV-DYN-006"].consequence

    # Full tables -> PASS_FULL; all partial on same-source / single-table -> PASS_SAMPLE_BOUNDED
    rep_full = prover.prove(
        ir,
        profs,
        source_rows_by_table=rows_clean,
        table_sample_flags={"src_a:accts": True, "src_b:attrs": True},
    )
    assert rep_full.real_data_proof_results["DV-DYN-006"].status == "PASS_FULL"
    assert rep_full.real_data_proof_results["DV-DYN-007"].status == "PASS_FULL"

    # 4. DV-DYN-007 SKIPPED_PARTIAL_SAMPLE when Link->Hub has partial Hub sample
    ir_lnk = _build_esat_ir(bitemporal=False)
    ts_lnk = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    profs_lnk = [
        SourceProfile(
            source_id="src_core",
            schema_fingerprint="fp_l",
            table_count=1,
            tables=[
                TableProfile(
                    table_id="src_core:acct_mgr",
                    table_name="acct_mgr",
                    row_count_est=1,
                    columns=[
                        ColumnProfile(col_name="acct_id", sql_type="VARCHAR", nullable=False, is_pk=True, null_ratio=0.0, distinct_count_est=1, sample_values=["A1"]),
                        ColumnProfile(col_name="mgr_id", sql_type="VARCHAR", nullable=False, is_pk=False, null_ratio=0.0, distinct_count_est=1, sample_values=["M1"]),
                    ],
                )
            ],
        )
    ]
    rows_lnk = {"src_core:acct_mgr": [{"acct_id": "A1", "mgr_id": "M1", "load_dts": ts_lnk}]}
    rep_lnk_partial = prover.prove(
        ir_lnk, profs_lnk, source_rows_by_table=rows_lnk, table_sample_flags={"src_core:acct_mgr": False}
    )
    assert rep_lnk_partial.real_data_proof_results["DV-DYN-007"].status == "SKIPPED_PARTIAL_SAMPLE"
    assert rep_lnk_partial.real_data_proof_results["DV-DYN-006"].status == "PASS_SAMPLE_BOUNDED"

    # 5. DV-DYN-004 multi-active RemediationHint suggesting MAS promotion with candidate_cdk
    rows_mas_dup = {
        "src_a:accts": [{"acct_id": tok_a, "load_dts": ts}],
        "src_b:attrs": [
            {"acct_id": tok_a, "seq_no": 1, "attr_val": "X", "load_dts": ts},
            {"acct_id": tok_a, "seq_no": 2, "attr_val": "Y", "load_dts": ts},
        ],
    }
    rep_mas = prover.prove(
        ir,
        profs,
        source_rows_by_table=rows_mas_dup,
        table_sample_flags={"src_a:accts": True, "src_b:attrs": True},
    )
    assert rep_mas.real_data_proof_results["DV-DYN-004"].status == "FAIL"
    assert "Suggested fix:" in rep_mas.real_data_proof_results["DV-DYN-004"].detail
    hints = [c for c in rep_mas.counterexamples if c.get("rule_id") == "DV-DYN-004" and "remediation_hint" in c]
    assert hints
    assert "sat_kind='MAS'" in str(hints[0]["remediation_hint"])
    assert "child_dependent_keys=['seq_no']" in str(hints[0]["remediation_hint"])

    # 6. scrub_prover_exception redacts literals, PII, long digits, and truncates to 500 chars
    raw_err = (
        "Binder Error: 'alice@example.com' SSN 123-45-6789 "
        'col="secret_val" ref 9876543210 ' + ("x" * 600)
    )
    scrubbed = scrub_prover_exception(raw_err)
    assert len(scrubbed) <= 500
    assert "alice@example.com" not in scrubbed
    assert "123-45-6789" not in scrubbed
    assert "secret_val" not in scrubbed
    assert "9876543210" not in scrubbed
    assert "'<REDACTED>'" in scrubbed
    assert '"<REDACTED>"' in scrubbed
