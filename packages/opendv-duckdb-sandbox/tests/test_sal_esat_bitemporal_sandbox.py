"""TDD tests for SAME_AS_LINK + EFFECTIVITY satellite seeding & bi-temporal selection."""

from __future__ import annotations

from opendv_core.constants import GHOST_REC_SRC, ZERO_KEY_ERROR, ZERO_KEY_UNKNOWN
from opendv_core.models.canonical_ir import (
    CanonicalIR,
    IRBusinessKeyColumn,
    IRColumnDef,
    IRHashColumn,
    IRHashConfig,
    IRHashExpression,
    IRHub,
    IRHubSourceMapping,
    IRLink,
    IRLinkHubReference,
    IRParentHkColumn,
    IRPitTable,
    IRSatellite,
    IRSourceMapping,
    IRSystemColumns,
)
from opendv_duckdb_sandbox.compiler_4zone import _find_bitemporal_satellite
from opendv_duckdb_sandbox.sandbox_engine import DuckDbShadowSandbox


def _build_sal_esat_ir(
    *,
    include_standard_sats: bool = True,
    standard_has_applied_dts: bool = False,
    esat_first: bool = True,
    include_bkcc_col: bool = False,
) -> CanonicalIR:
    bks = (
        [
            IRBusinessKeyColumn(name="bkcc", data_type="VARCHAR(64)", nullable=False, is_bkcc=True),
            IRBusinessKeyColumn(name="entity_code", data_type="VARCHAR(255)", nullable=False),
        ]
        if include_bkcc_col
        else [IRBusinessKeyColumn(name="entity_code", data_type="VARCHAR(255)", nullable=False)]
    )
    hub = IRHub(
        name="hub_clearing_entity",
        business_concept="Clearing Entity",
        verified_global_master_key=True,
        hk_column=IRHashColumn(
            name="hk_clearing_entity",
            data_type="BINARY(32)",
            nullable=False,
            hash_expression=IRHashExpression(
                algorithm="SHA256",
                input_columns=[bk.name for bk in bks],
                normalization="UPPER_TRIM",
            ),
        ),
        business_keys=bks,
        load_dts_column=IRColumnDef(name="load_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False),
        rec_src_column=IRColumnDef(name="rec_src", data_type="VARCHAR(255)", nullable=False),
        source_mappings=[
            IRHubSourceMapping(
                staging_table="stg_clearing_entities",
                bk_source_columns=[bk.name for bk in bks],
                rec_src_value="CLEARING_CORE",
            )
        ],
    )
    sal_link = IRLink(
        name="sal_clearing_entity",
        link_type="SAME_AS",
        semantic_role="Canonical entity convergence",
        hk_column=IRHashColumn(
            name="hk_sal_clearing_entity",
            data_type="BINARY(32)",
            nullable=False,
            hash_expression=IRHashExpression(
                algorithm="SHA256",
                input_columns=["hk_clearing_entity_master", "hk_clearing_entity_duplicate"],
                normalization="UPPER_TRIM",
            ),
        ),
        hub_references=[
            IRLinkHubReference(
                fk_column_name="hk_clearing_entity_master",
                target_hub="hub_clearing_entity",
                target_hk_column="hk_clearing_entity",
                data_type="BINARY(32)",
                nullable=False,
                role_name="master",
            ),
            IRLinkHubReference(
                fk_column_name="hk_clearing_entity_duplicate",
                target_hub="hub_clearing_entity",
                target_hk_column="hk_clearing_entity",
                data_type="BINARY(32)",
                nullable=False,
                role_name="duplicate",
            ),
        ],
        degenerate_columns=[],
        immutable_measures=[],
        load_dts_column=IRColumnDef(name="load_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False),
        rec_src_column=IRColumnDef(name="rec_src", data_type="VARCHAR(255)", nullable=False),
        source_mappings=[
            {
                "staging_table": "stg_clearing_crosswalk",
                "fk_source_columns": {
                    "hk_clearing_entity_master": "master_entity_code",
                    "hk_clearing_entity_duplicate": "duplicate_entity_code",
                },
                "rec_src_value": "MDM_CROSSWALK",
            }
        ],
    )
    esat = IRSatellite(
        name="esat_sal_clearing_entity",
        satellite_type="EFFECTIVITY",
        split_classification="CORE",
        parent_entity="sal_clearing_entity",
        parent_hk_column=IRParentHkColumn(
            name="hk_sal_clearing_entity",
            target_column="hk_sal_clearing_entity",
            data_type="BINARY(32)",
            nullable=False,
        ),
        driving_keys=["hk_clearing_entity_master"],
        load_dts_column=IRColumnDef(name="load_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False),
        applied_dts_column=IRColumnDef(
            name="applied_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False
        ),
        rec_src_column=IRColumnDef(name="rec_src", data_type="VARCHAR(255)", nullable=False),
        hash_diff_column=IRHashColumn(
            name="hash_diff",
            data_type="BINARY(32)",
            nullable=False,
            hash_expression=IRHashExpression(
                algorithm="SHA256",
                input_columns=["effective_from_dts", "effective_to_dts", "is_active_flag"],
                normalization="EXACT_TRIM",
            ),
        ),
        hash_diff_migration_policy="SPLIT_NEW_SATELLITE",
        payload_columns=[
            IRColumnDef(name="effective_from_dts", data_type="TIMESTAMP_NTZ(9)", nullable=True),
            IRColumnDef(name="effective_to_dts", data_type="TIMESTAMP_NTZ(9)", nullable=True),
            IRColumnDef(name="is_active_flag", data_type="BOOLEAN", nullable=True),
        ],
        source_mapping=IRSourceMapping(
            staging_table="stg_clearing_crosswalk",
            rec_src_value="MDM_CROSSWALK",
        ),
    )
    std_sats: list[IRSatellite] = []
    pits: list[IRPitTable] = []
    if include_standard_sats:
        applied_col = (
            IRColumnDef(name="applied_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False)
            if standard_has_applied_dts
            else None
        )
        std_sat = IRSatellite(
            name="sat_clearing_entity_core",
            satellite_type="STANDARD",
            split_classification="CORE",
            parent_entity="hub_clearing_entity",
            parent_hk_column=IRParentHkColumn(
                name="hk_clearing_entity",
                target_column="hk_clearing_entity",
                data_type="BINARY(32)",
                nullable=False,
            ),
            load_dts_column=IRColumnDef(
                name="load_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False
            ),
            applied_dts_column=applied_col,
            rec_src_column=IRColumnDef(name="rec_src", data_type="VARCHAR(255)", nullable=False),
            hash_diff_column=IRHashColumn(
                name="hash_diff",
                data_type="BINARY(32)",
                nullable=False,
                hash_expression=IRHashExpression(
                    algorithm="SHA256",
                    input_columns=["entity_status", "risk_tier"],
                    normalization="EXACT_TRIM",
                ),
            ),
            hash_diff_migration_policy="SPLIT_NEW_SATELLITE",
            payload_columns=[
                IRColumnDef(name="entity_status", data_type="VARCHAR(255)", nullable=True),
                IRColumnDef(name="risk_tier", data_type="VARCHAR(255)", nullable=True),
            ],
            source_mapping=IRSourceMapping(
                staging_table="stg_clearing_entities",
                rec_src_value="CLEARING_CORE",
            ),
        )
        std_sats.append(std_sat)
        pits.append(
            IRPitTable(
                name="pit_clearing_entity",
                parent_entity="hub_clearing_entity",
                as_of_dts_column="as_of_dts",
                snapshot_window_policy="LOGARITHMIC",
                tracked_satellites=["sat_clearing_entity_core"],
            )
        )

    satellites = [esat, *std_sats] if esat_first else [*std_sats, esat]
    return CanonicalIR(
        ir_version="1.0.0",
        model_name="clearing_sal_esat_vault",
        hash_config=IRHashConfig(
            algorithm="SHA256",
            storage_type="BINARY(32)",
            delimiter="||",
            null_token_bk="-1",
            null_token_payload="-1:^^",
            zero_key_unknown_token="-1",
            zero_key_error_token="-2",
            encoding_scheme="LENGTH_PREFIXED",
        ),
        system_columns=IRSystemColumns(
            load_dts_col="load_dts",
            load_dts_type="TIMESTAMP_NTZ(9)",
            rec_src_col="rec_src",
            rec_src_type="VARCHAR(255)",
            hash_diff_col="hash_diff",
            applied_dts_col="applied_dts",
        ),
        hubs=[hub],
        links=[sal_link],
        satellites=satellites,
        pit_tables=pits,
        bridge_tables=[],
        reference_tables=[],
    )


def _assert_esat_matches_sal_distinct(sandbox: DuckDbShadowSandbox) -> None:
    rows = sandbox.conn.execute(
        f"""
        SELECT
            s.hk_sal_clearing_entity,
            s.hk_clearing_entity_master,
            s.hk_clearing_entity_duplicate
        FROM business_vault.esat_sal_clearing_entity e
        INNER JOIN business_vault.sal_clearing_entity s
            ON s.hk_sal_clearing_entity = e.hk_sal_clearing_entity
        WHERE e.rec_src <> '{GHOST_REC_SRC}'
          AND s.hk_sal_clearing_entity NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
        """
    ).fetchall()
    assert len(rows) >= 1, (
        "Expected non-ghost rows in esat_sal_clearing_entity matching sal_clearing_entity"
    )
    for _sal_hk, master_hk, dup_hk in rows:
        assert master_hk != dup_hk


def test_sal_with_esat_and_standard_sat_without_applied_dts_passes_full_sandbox() -> None:
    """Reproduce STAGE_06 Batch 4 crash: esat_sal_* has applied_dts, raw_vault sat does not."""
    ir = _build_sal_esat_ir(
        include_standard_sats=True,
        standard_has_applied_dts=False,
        esat_first=True,
        include_bkcc_col=False,
    )
    with DuckDbShadowSandbox() as sandbox:
        report = sandbox.run_full_verification(ir, emit_telemetry=False)
        assert report.passed is True
        assert report.batch_report.all_passed is True
        assert report.dynamic_check_report.all_passed is True
        assert report.bitemporal_proof.verified is True
        _assert_esat_matches_sal_distinct(sandbox)


def test_sal_with_esat_and_standard_sat_with_applied_dts_prefers_standard_sat() -> None:
    """When both raw_vault STANDARD sat and esat_sal_* have applied_dts, pick STANDARD sat."""
    ir = _build_sal_esat_ir(
        include_standard_sats=True,
        standard_has_applied_dts=True,
        esat_first=True,
        include_bkcc_col=True,
    )
    chosen = _find_bitemporal_satellite(ir)
    assert chosen is not None
    assert chosen.name == "sat_clearing_entity_core"

    with DuckDbShadowSandbox() as sandbox:
        report = sandbox.run_full_verification(ir, emit_telemetry=False)
        assert report.passed is True
        assert report.batch_report.all_passed is True
        assert report.dynamic_check_report.all_passed is True
        assert report.bitemporal_proof.verified is True
        _assert_esat_matches_sal_distinct(sandbox)


def test_sal_with_only_effectivity_satellite_passes_full_sandbox() -> None:
    """When only esat_sal_* exists (no STANDARD sats), full 4-batch verification passes."""
    ir = _build_sal_esat_ir(
        include_standard_sats=False,
        standard_has_applied_dts=False,
        esat_first=True,
        include_bkcc_col=True,
    )
    chosen = _find_bitemporal_satellite(ir)
    assert chosen is not None
    assert chosen.name == "esat_sal_clearing_entity"

    with DuckDbShadowSandbox() as sandbox:
        report = sandbox.run_full_verification(ir, emit_telemetry=False)
        assert report.passed is True
        assert report.batch_report.all_passed is True
        assert report.dynamic_check_report.all_passed is True
        assert report.bitemporal_proof.verified is True
        _assert_esat_matches_sal_distinct(sandbox)
