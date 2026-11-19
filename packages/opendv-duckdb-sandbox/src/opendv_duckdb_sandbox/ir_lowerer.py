"""Canonical IR Lowerer (`NoMistakeDataVault2ContractV5` -> `CanonicalIR`).

Lowers a verified `NoMistakeDataVault2ContractV5` model contract (and optional `GraphBProfile`)
into a schema-valid `CanonicalIR` (`schemas/dv2-canonical-ir.schema.json`) and computes the
deterministic topological load DAG (`hub_*` / `ref_*` -> `lnk_*` / `sal_*` -> `sat_*` / `esat_*`
-> `pit_*` / `brg_*`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from opendv_core.models.canonical_ir import (
    CanonicalIR,
    IRBridgeTable,
    IRColumnDef,
    IRHashColumn,
    IRHashConfig,
    IRHashExpression,
    IRHub,
    IRLink,
    IRLinkHubReference,
    IRParentHkColumn,
    IRPitTable,
    IRReferenceTable,
    IRSatellite,
    IRSourceMapping,
    IRSystemColumns,
)
from opendv_core.models.contract_v5 import (
    ContractLinkSpec,
    ContractSatelliteSpec,
    NoMistakeDataVault2ContractV5,
)
from opendv_core.models.graph_b import GraphBProfile
from opendv_core.schema_registry import load_structured_file, validate_canonical_ir
from opendv_duckdb_sandbox.ir_lowerer_helpers import (
    _derive_hub_hk_name,
    _extract_profile_column_types,
    _infer_sql_data_type,
    _lower_hub,
    _resolve_link_ref_bkcc_literal,
    _sanitize_model_name,
    _staging_table_name,
)

_LINK_TYPE_MAP: dict[str, str] = {
    "STANDARD_LINK": "STANDARD",
    "HIERARCHICAL_LINK": "HIERARCHICAL",
    "SAME_AS_LINK": "SAME_AS",
    "NON_HISTORIZED_LINK": "NON_HISTORIZED",
    "EXPLORATION_LINK": "EXPLORATION",
    "UNSTRUCTURED_EXTRACTION_LINK": "UNSTRUCTURED_EXTRACTION_LINK",
}

_SAT_TYPE_MAP: dict[str, str] = {
    "STANDARD_SATELLITE": "STANDARD",
    "BUSINESS_SATELLITE": "BUSINESS",
    "MULTI_ACTIVE_SATELLITE": "MULTI_ACTIVE",
    "EFFECTIVITY_SATELLITE": "EFFECTIVITY",
    "STATUS_TRACKING_SATELLITE": "STATUS_TRACKING",
    "RECORD_TRACKING_SATELLITE": "RECORD_TRACKING",
}


def _lower_link(
    contract_link: ContractLinkSpec,
    hub_hk_map: dict[str, str],
    bronze_to_rec_src: dict[str, str],
    scoped_bkcc_map: dict[tuple[str, str, str], str | None] | None = None,
    hub_source_specs: dict[str, list[tuple[list[str], str, str | None]]] | None = None,
) -> IRLink:
    """Lower a `ContractLinkSpec` into a schema-valid `IRLink`."""
    ir_link_type = _LINK_TYPE_MAP.get(contract_link.link_type, "STANDARD")
    hk_name = f"hk_{contract_link.name}"

    hub_references: list[IRLinkHubReference] = []
    fk_source_columns: dict[str, str] = {}
    fk_bkcc_literals: dict[str, str | None] = {}
    seen_hub_counts: dict[str, int] = {}
    total_occurrences: dict[str, int] = {}
    for ref in contract_link.hub_references:
        total_occurrences[ref.target_hub] = total_occurrences.get(ref.target_hub, 0) + 1

    for ref in contract_link.hub_references:
        h_name = ref.target_hub
        idx = seen_hub_counts.get(h_name, 0)
        seen_hub_counts[h_name] = idx + 1
        target_hk = hub_hk_map.get(h_name, _derive_hub_hk_name(h_name))
        role_slug = ref.role_alias.lower()

        if total_occurrences[h_name] > 1:
            fk_col_name = f"{target_hk}_{role_slug}"
            role_name = role_slug
        else:
            fk_col_name = target_hk
            role_name = role_slug or h_name.removeprefix("hub_").removeprefix("bhub_")

        src_fk_col = ref.source_fk_columns[0] if ref.source_fk_columns else fk_col_name
        fk_source_columns[fk_col_name] = src_fk_col
        fk_bkcc_literals[fk_col_name] = _resolve_link_ref_bkcc_literal(
            contract_link.source_bronze_table,
            src_fk_col,
            h_name,
            role_alias=role_name,
            scoped_bkcc_map=scoped_bkcc_map or {},
            hub_source_specs=hub_source_specs or {},
        )

        hub_references.append(
            IRLinkHubReference(
                fk_column_name=fk_col_name,
                target_hub=h_name,
                target_hk_column=target_hk,
                data_type="BINARY(32)",
                nullable=False,
                role_name=role_name,
            )
        )

    degen_cols = [
        IRColumnDef(
            name=col_name,
            data_type="VARCHAR(255)",
            nullable=False,
        )
        for col_name in (contract_link.degenerate_columns or [])
    ]

    rec_src_val = bronze_to_rec_src.get(
        contract_link.source_bronze_table,
        contract_link.source_bronze_table.split(".")[0].upper(),
    )
    staging_tbl = _staging_table_name(contract_link.source_bronze_table)
    is_unstructured_extraction = contract_link.link_type == "UNSTRUCTURED_EXTRACTION_LINK"
    steward_pred: str | None = None
    source_mapping_entry: dict[str, Any] = {
        "staging_table": staging_tbl,
        "bronze_table": contract_link.source_bronze_table,
        "fk_source_columns": fk_source_columns,
        "fk_bkcc_literals": fk_bkcc_literals,
        "rec_src_value": rec_src_val,
    }
    if contract_link.where_filter_condition:
        source_mapping_entry["where_filter_condition"] = contract_link.where_filter_condition
    if is_unstructured_extraction:
        steward_pred = contract_link.where_filter_condition or "steward_gate_status = 'APPROVED'"
        source_mapping_entry["unstructured_extraction_link"] = True
        source_mapping_entry["requires_steward_approval_gate"] = True
        source_mapping_entry["steward_gate_filter"] = "src.steward_gate_status = 'APPROVED'"
        source_mapping_entry["steward_filter_predicate"] = steward_pred

    return IRLink(
        name=contract_link.name,
        link_type=ir_link_type,  # type: ignore[arg-type]
        semantic_role=(
            f"Unstructured extraction relationship {contract_link.name} sourced from "
            f"{contract_link.source_bronze_table}"
            if is_unstructured_extraction
            else (
                f"Relationship {contract_link.name} sourced from "
                f"{contract_link.source_bronze_table}"
            )
        ),
        requires_steward_approval_gate=is_unstructured_extraction,
        steward_filter_predicate=steward_pred,
        max_recursion_depth=10 if ir_link_type == "HIERARCHICAL" else None,
        cycle_prevention_predicate=True if ir_link_type == "HIERARCHICAL" else None,
        hk_column=IRHashColumn(
            name=hk_name,
            data_type="BINARY(32)",
            nullable=False,
            hash_expression=IRHashExpression(
                algorithm="SHA256",
                input_columns=[r.fk_column_name for r in hub_references]
                + [d.name for d in degen_cols],
                normalization="UPPER_TRIM",
            ),
        ),
        hub_references=hub_references,
        degenerate_columns=degen_cols,
        immutable_measures=[],
        load_dts_column=IRColumnDef(name="load_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False),
        rec_src_column=IRColumnDef(name="rec_src", data_type="VARCHAR(255)", nullable=False),
        source_mappings=[source_mapping_entry],
    )


def _classify_satellite_split(sat: ContractSatelliteSpec) -> str:
    """Map `ContractSatelliteSpec` security/volatility to CanonicalIR `split_classification`."""
    if sat.security_classification in {"PII_HIGH", "CONFIDENTIAL", "PCI"}:
        return "PII_RESTRICTED"
    if sat.volatility_tier == "HIGH_FREQ":
        return "HIGH_VELOCITY"
    if sat.volatility_tier == "STATIC":
        return "STATIC"
    if sat.satellite_type in {"AI_PROVENANCE_SATELLITE", "VECTOR_SATELLITE"}:
        return "DERIVED_BV"
    return "CORE"


def _lower_satellite(
    contract_sat: ContractSatelliteSpec,
    entity_hk_map: dict[str, str],
    bronze_to_rec_src: dict[str, str],
    profile_types: dict[str, str],
) -> IRSatellite:
    """Lower a `ContractSatelliteSpec` into a schema-valid `IRSatellite`."""
    ir_sat_type = _SAT_TYPE_MAP.get(contract_sat.satellite_type, "STANDARD")
    split_cls = _classify_satellite_split(contract_sat)
    parent_hk = entity_hk_map.get(
        contract_sat.parent_entity,
        _derive_hub_hk_name(contract_sat.parent_entity),
    )

    payload_cols = [
        IRColumnDef(
            name=col_name,
            data_type=_infer_sql_data_type(col_name, profile_types),
            nullable=True,
        )
        for col_name in contract_sat.payload_columns
    ]

    cdk_cols = (
        [
            IRColumnDef(
                name=contract_sat.sub_sequence_column,
                data_type=_infer_sql_data_type(contract_sat.sub_sequence_column, profile_types),
                nullable=False,
            )
        ]
        if contract_sat.sub_sequence_column
        else None
    )

    driving_keys = [contract_sat.driving_key_column] if contract_sat.driving_key_column else None

    has_applied_dts = any(
        col.name
        in {
            "applied_dts",
            "effective_from_date",
            "effective_from_dt",
            "effective_from_dts",
            "effective_date",
            "as_of_quarter_end",
            "call_due_date",
            "reporting_quarter",
            "commitment_date",
        }
        for col in payload_cols
    )
    applied_dts_col = (
        IRColumnDef(name="applied_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False)
        if has_applied_dts
        else None
    )

    rec_src_val = bronze_to_rec_src.get(
        contract_sat.source_bronze_table,
        contract_sat.source_bronze_table.split(".")[0].upper(),
    )

    return IRSatellite(
        name=contract_sat.name,
        satellite_type=ir_sat_type,  # type: ignore[arg-type]
        split_classification=split_cls,  # type: ignore[arg-type]
        parent_entity=contract_sat.parent_entity,
        parent_hk_column=IRParentHkColumn(
            name=parent_hk,
            target_column=parent_hk,
            data_type="BINARY(32)",
            nullable=False,
        ),
        child_dependent_keys=cdk_cols,
        driving_keys=driving_keys,
        load_dts_column=IRColumnDef(name="load_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False),
        applied_dts_column=applied_dts_col,
        rec_src_column=IRColumnDef(name="rec_src", data_type="VARCHAR(255)", nullable=False),
        hash_diff_column=IRHashColumn(
            name="hash_diff",
            data_type="BINARY(32)",
            nullable=False,
            hash_expression=IRHashExpression(
                algorithm="SHA256",
                input_columns=[c.name for c in payload_cols],
                normalization="EXACT_TRIM",
            ),
        ),
        hash_diff_migration_policy=contract_sat.hash_diff_migration_policy or "SPLIT_NEW_SATELLITE",
        payload_columns=payload_cols,
        source_mapping=IRSourceMapping(
            staging_table=_staging_table_name(contract_sat.source_bronze_table),
            rec_src_value=rec_src_val,
            soft_deprecated_columns=(
                list(contract_sat.soft_deprecated_columns)
                if contract_sat.soft_deprecated_columns
                else None
            ),
        ),
    )


def lower_contract_to_canonical_ir(
    contract: NoMistakeDataVault2ContractV5 | dict[str, Any] | str | Path,
    *,
    graph_b_profile: GraphBProfile | dict[str, Any] | None = None,
    model_name: str | None = None,
    pit_tables: list[IRPitTable] | None = None,
    bridge_tables: list[IRBridgeTable] | None = None,
    validate_schema: bool = True,
) -> CanonicalIR:
    """Lower a verified `NoMistakeDataVault2ContractV5` into a validated `CanonicalIR`."""
    if isinstance(contract, (str, Path)):
        raw_contract = load_structured_file(contract)
        contract_obj = NoMistakeDataVault2ContractV5.model_validate(raw_contract)
    elif isinstance(contract, dict):
        contract_obj = NoMistakeDataVault2ContractV5.model_validate(contract)
    else:
        contract_obj = contract

    if isinstance(graph_b_profile, dict):
        profile_obj: GraphBProfile | None = GraphBProfile.model_validate(graph_b_profile)
    else:
        profile_obj = graph_b_profile

    profile_types = _extract_profile_column_types(profile_obj)

    ir_hubs: list[IRHub] = []
    entity_hk_map: dict[str, str] = {}
    bronze_to_rec_src: dict[str, str] = {}
    scoped_bkcc_map: dict[tuple[str, str, str], str | None] = {}
    hub_source_specs: dict[str, list[tuple[list[str], str, str | None]]] = {}

    for c_hub in contract_obj.hubs:
        ir_hub, hub_rec_map, hub_bkcc_map, hub_specs = _lower_hub(c_hub, profile_types)
        ir_hubs.append(ir_hub)
        entity_hk_map[ir_hub.name] = ir_hub.hk_column.name
        for k, v in hub_rec_map.items():
            bronze_to_rec_src.setdefault(k, v)
        scoped_bkcc_map.update(hub_bkcc_map)
        hub_source_specs[c_hub.name] = hub_specs

    ir_links: list[IRLink] = []
    for c_link in contract_obj.links:
        ir_link = _lower_link(
            c_link,
            entity_hk_map,
            bronze_to_rec_src,
            scoped_bkcc_map=scoped_bkcc_map,
            hub_source_specs=hub_source_specs,
        )
        ir_links.append(ir_link)
        entity_hk_map[ir_link.name] = ir_link.hk_column.name

    ir_sats: list[IRSatellite] = []
    sats_by_parent: dict[str, list[str]] = {}
    for c_sat in contract_obj.satellites:
        ir_sat = _lower_satellite(c_sat, entity_hk_map, bronze_to_rec_src, profile_types)
        ir_sats.append(ir_sat)
        sats_by_parent.setdefault(ir_sat.parent_entity, []).append(ir_sat.name)

    ir_refs: list[IRReferenceTable] = [
        IRReferenceTable(
            name=c_ref.name,
            natural_code_column=c_ref.code_column,
            satellites=[],
        )
        for c_ref in contract_obj.reference_tables
    ]

    if pit_tables is None:
        inferred_pits: list[IRPitTable] = []
        for ir_hub in ir_hubs:
            attached = sats_by_parent.get(ir_hub.name, [])
            if len(attached) >= 2:
                slug = ir_hub.name.removeprefix("hub_").removeprefix("bhub_")
                inferred_pits.append(
                    IRPitTable(
                        name=f"pit_{slug}",
                        parent_entity=ir_hub.name,
                        as_of_dts_column="as_of_dts",
                        snapshot_window_policy="LOGARITHMIC",
                        tracked_satellites=attached,
                    )
                )
        pit_tables = inferred_pits

    canonical_ir = CanonicalIR(
        ir_version="1.0.0",
        model_name=model_name or _sanitize_model_name(contract_obj.domain_namespace),
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
        hubs=ir_hubs,
        links=ir_links,
        satellites=ir_sats,
        pit_tables=pit_tables,
        bridge_tables=bridge_tables or [],
        reference_tables=ir_refs,
    )

    if validate_schema:
        payload = canonical_ir.model_dump(mode="json", exclude_none=True)
        validate_canonical_ir(payload)

    return canonical_ir


def compute_topological_load_waves(ir: CanonicalIR) -> dict[str, list[str]]:
    """Partition CanonicalIR entities into deterministic parallel load waves.

    Wave 1: Reference tables & Hubs (`ref_*`, `hub_*`, `bhub_*`)
    Wave 2: Links (`lnk_*`, `t_lnk_*`, `nh_lnk_*`, `sal_*`, `hal_*`, `x_lnk_*`)
    Wave 3: Satellites (`sat_*`, `bsat_*`, `mas_*`, `ma_sat_*`, `esat_*`, `sts_*`, `rts_*`)
    Wave 4: Information Mart accelerators (`pit_*`, `brg_*`)
    """
    wave_1 = sorted([r.name for r in (ir.reference_tables or [])] + [h.name for h in ir.hubs])
    wave_2 = sorted(lnk.name for lnk in ir.links)
    wave_3 = sorted(sat.name for sat in ir.satellites)
    wave_4 = sorted(
        [p.name for p in (ir.pit_tables or [])] + [b.name for b in (ir.bridge_tables or [])]
    )
    return {
        "wave_1_hubs_and_refs": wave_1,
        "wave_2_links": wave_2,
        "wave_3_satellites": wave_3,
        "wave_4_pits_and_bridges": wave_4,
    }


def topological_load_dag(ir: CanonicalIR) -> list[str]:
    """Return a deterministic topological load order across all entities in `CanonicalIR`."""
    waves = compute_topological_load_waves(ir)
    return (
        waves["wave_1_hubs_and_refs"]
        + waves["wave_2_links"]
        + waves["wave_3_satellites"]
        + waves["wave_4_pits_and_bridges"]
    )
