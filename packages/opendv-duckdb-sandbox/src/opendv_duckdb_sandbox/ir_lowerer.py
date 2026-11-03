"""Canonical IR Lowerer (`NoMistakeDataVault2ContractV5` -> `CanonicalIR`).

Lowers a verified `NoMistakeDataVault2ContractV5` model contract (and optional `GraphBProfile`)
into a schema-valid `CanonicalIR` (`schemas/dv2-canonical-ir.schema.json`) and computes the
deterministic topological load DAG (`hub_*` / `ref_*` -> `lnk_*` / `sal_*` -> `sat_*` / `esat_*`
-> `pit_*` / `brg_*`).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from opendv_core.models.canonical_ir import (
    CanonicalIR,
    IRBridgeTable,
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
    IRReferenceTable,
    IRSatellite,
    IRSourceMapping,
    IRSystemColumns,
)
from opendv_core.models.contract_v5 import (
    ContractHubSpec,
    ContractLinkSpec,
    ContractSatelliteSpec,
    NoMistakeDataVault2ContractV5,
)
from opendv_core.models.graph_b import GraphBProfile
from opendv_core.schema_registry import load_structured_file, validate_canonical_ir

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


def _sanitize_model_name(domain_namespace: str) -> str:
    """Normalize a domain namespace into a schema-valid `^[a-z][a-z0-9_]*$` model name."""
    cleaned = re.sub(r"[^a-z0-9_]+", "_", domain_namespace.lower()).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"dv_{cleaned}" if cleaned else "dv_model"
    return cleaned if cleaned.endswith("_vault") else f"{cleaned}_vault"


def _staging_table_name(bronze_table: str) -> str:
    """Derive a canonical Zone 1 staging view name from a qualified Bronze table name."""
    parts = bronze_table.split(".")
    leaf = parts[-1].lower()
    for prefix in ("src_", "raw_", "bronze_"):
        if leaf.startswith(prefix) and len(leaf) > len(prefix):
            leaf = leaf[len(prefix) :]
            break
    if len(parts) >= 2:
        src_prefix = parts[0].lower().removeprefix("bronze_")
        if not leaf.startswith(src_prefix):
            return f"stg_{src_prefix}_{leaf}"
    return f"stg_{leaf}"


def _infer_sql_data_type(column_name: str, profile_type_map: dict[str, str] | None = None) -> str:
    """Infer a canonical SQL data type for a column from Graph B or naming conventions."""
    if profile_type_map and column_name in profile_type_map:
        return profile_type_map[column_name]
    lower = column_name.lower()
    if lower.endswith(("_usd", "_sgd", "_eur", "_amount", "_amt", "_nav", "_ebitda", "_ev")):
        return "DECIMAL(18,2)"
    if lower.endswith(("_pct", "_rate", "_multiple", "_ratio", "_score", "_confidence")):
        return "DECIMAL(10,4)"
    if lower.endswith(("_date", "_dt")):
        return "DATE"
    if lower.endswith(("_ts", "_dts", "_timestamp")):
        return "TIMESTAMP_NTZ(9)"
    if lower.startswith(("is_", "has_")) or lower.endswith("_flag"):
        return "BOOLEAN"
    if lower.endswith(("_year", "_bps", "_count", "_index", "_page", "_seq")):
        return "INTEGER"
    if lower.endswith("_sha256"):
        return "CHAR(64)"
    if lower.endswith(("_quote", "_excerpt", "_text", "_uri")):
        return "VARCHAR(2000)"
    return "VARCHAR(255)"


def _extract_profile_column_types(graph_b: GraphBProfile | None) -> dict[str, str]:
    """Build a lookup map of `column_name -> data_type` from a `GraphBProfile`."""
    if graph_b is None:
        return {}
    type_map: dict[str, str] = {}
    for system in graph_b.source_systems:
        for table in system.tables:
            for col in table.columns:
                type_map[col.column_name] = col.data_type
    return type_map


def _derive_hub_hk_name(hub_name: str) -> str:
    """Derive a canonical `hk_<concept>` column name from a Hub table name."""
    slug = hub_name
    for prefix in ("bhub_", "hub_"):
        if slug.startswith(prefix):
            slug = slug[len(prefix) :]
            break
    # Abbreviate well-known long concept names consistently
    abbreviations = {
        "limited_partner": "lp",
        "portfolio_company": "portco",
        "capital_account": "cap_acct",
        "unstructured_document": "doc",
    }
    return f"hk_{abbreviations.get(slug, slug)}"


def _lower_hub(
    contract_hub: ContractHubSpec,
    profile_types: dict[str, str],
) -> tuple[IRHub, dict[str, str]]:
    """Lower a `ContractHubSpec` into an `IRHub` and return `(ir_hub, bronze_to_rec_src_map)`."""
    hk_name = _derive_hub_hk_name(contract_hub.name)
    verified_global = all(
        src.key_authority_tier == "TIER_1_GLOBAL_STANDARD" and not src.require_src_qualifier
        for src in contract_hub.business_key_sources
    )

    primary_key_cols = contract_hub.business_key_sources[0].key_columns
    business_keys: list[IRBusinessKeyColumn] = []
    if not verified_global:
        business_keys.append(
            IRBusinessKeyColumn(
                name="bkcc",
                data_type="VARCHAR(64)",
                nullable=False,
                is_bkcc=True,
            )
        )
    for key_col in primary_key_cols:
        business_keys.append(
            IRBusinessKeyColumn(
                name=key_col,
                data_type=_infer_sql_data_type(key_col, profile_types),
                nullable=False,
            )
        )

    source_mappings: list[IRHubSourceMapping] = []
    bronze_to_rec_src: dict[str, str] = {}
    for src in contract_hub.business_key_sources:
        bronze_to_rec_src[src.bronze_table] = src.record_source_code
        bk_src_cols = (["bkcc"] if not verified_global else []) + list(src.key_columns)
        source_mappings.append(
            IRHubSourceMapping(
                staging_table=_staging_table_name(src.bronze_table),
                bk_source_columns=bk_src_cols,
                rec_src_value=src.record_source_code,
            )
        )

    concept_label = contract_hub.ontology_concept_uri.rsplit(":", 1)[-1].replace("_", " ").title()
    ir_hub = IRHub(
        name=contract_hub.name,
        business_concept=concept_label,
        verified_global_master_key=verified_global,
        hk_column=IRHashColumn(
            name=hk_name,
            data_type="BINARY(32)",
            nullable=False,
            hash_expression=IRHashExpression(
                algorithm="SHA256",
                input_columns=[bk.name for bk in business_keys],
                normalization="UPPER_TRIM",
            ),
        ),
        business_keys=business_keys,
        load_dts_column=IRColumnDef(name="load_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False),
        rec_src_column=IRColumnDef(name="rec_src", data_type="VARCHAR(255)", nullable=False),
        source_mappings=source_mappings,
    )
    return ir_hub, bronze_to_rec_src


def _lower_link(
    contract_link: ContractLinkSpec,
    hub_hk_map: dict[str, str],
    bronze_to_rec_src: dict[str, str],
) -> IRLink:
    """Lower a `ContractLinkSpec` into a schema-valid `IRLink`."""
    ir_link_type = _LINK_TYPE_MAP.get(contract_link.link_type, "STANDARD")
    hk_name = f"hk_{contract_link.name}"

    hub_references: list[IRLinkHubReference] = []
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
        "fk_source_columns": {r.fk_column_name: r.fk_column_name for r in hub_references},
        "rec_src_value": rec_src_val,
    }
    if is_unstructured_extraction:
        steward_pred = (
            contract_link.where_filter_condition or "steward_gate_status = 'APPROVED'"
        )
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
        in {"effective_from_date", "effective_from_dt", "as_of_quarter_end", "call_due_date"}
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
        hash_diff_migration_policy="SPLIT_NEW_SATELLITE",
        payload_columns=payload_cols,
        source_mapping=IRSourceMapping(
            staging_table=_staging_table_name(contract_sat.source_bronze_table),
            rec_src_value=rec_src_val,
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

    for c_hub in contract_obj.hubs:
        ir_hub, hub_rec_map = _lower_hub(c_hub, profile_types)
        ir_hubs.append(ir_hub)
        entity_hk_map[ir_hub.name] = ir_hub.hk_column.name
        bronze_to_rec_src.update(hub_rec_map)

    ir_links: list[IRLink] = []
    for c_link in contract_obj.links:
        ir_link = _lower_link(c_link, entity_hk_map, bronze_to_rec_src)
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
