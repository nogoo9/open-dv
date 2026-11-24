"""Apache Iceberg v2 metadata & schema evolution bundle emitter (`EVAL-FMT-01`).

Strictly Layer 2a: imports only opendv.models, dialects, sql_compiler.ddl_builders, stdlib.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opendv.models.project import EvolutionLockSpec, TargetManifest
from opendv.models.vault_ir import ArrayType, DecimalType, MapType, PortableLogicalType
from opendv.models.vault_ir_certified import CertifiedVaultIR, IRField
from opendv.storage.dialects import assign_physical_aliases, lower_logical_type
from opendv.storage.sql_compiler.ddl_builders import ZONE_SCHEMAS, planned_exclusion_sets


def _iceberg_type_json(
    ltype: PortableLogicalType,
    next_id: list[int],
) -> str | dict[str, Any]:
    if isinstance(ltype, ArrayType):
        eid = next_id[0]
        next_id[0] += 1
        return {
            "type": "list",
            "element-id": eid,
            "element-required": False,
            "element": _iceberg_type_json(ltype.element_type, next_id),
        }
    if isinstance(ltype, MapType):
        kid = next_id[0]
        vid = next_id[0] + 1
        next_id[0] += 2
        return {
            "type": "map",
            "key-id": kid,
            "key": _iceberg_type_json(ltype.key_type, next_id),
            "value-id": vid,
            "value-required": False,
            "value": _iceberg_type_json(ltype.value_type, next_id),
        }
    if isinstance(ltype, DecimalType):
        return f"decimal({ltype.precision},{ltype.scale})"
    return lower_logical_type(ltype, "iceberg")


def _build_iceberg_fields(columns: list[IRField]) -> tuple[list[dict[str, Any]], int]:
    max_col_id = max((c.field_id for c in columns), default=0)
    nested_counter = [max_col_id + 1]
    fields: list[dict[str, Any]] = []
    for col in columns:
        fields.append(
            {
                "id": col.field_id,
                "name": col.name,
                "required": not col.nullable,
                "type": _iceberg_type_json(col.logical_type, nested_counter),
            }
        )
    last_id = max(max_col_id, nested_counter[0] - 1)
    return fields, last_id


@dataclass(frozen=True, slots=True)
class IcebergBundle:
    """Generated Iceberg v2 metadata files and TargetManifest."""

    files: dict[str, str]
    manifest: TargetManifest


class IcebergEmitter:
    """Emit Apache Iceberg format-version 2 table metadata and wave schema ops."""

    def emit(
        self,
        ir: CertifiedVaultIR,
        *,
        prior_ir: CertifiedVaultIR | None = None,
        locks: EvolutionLockSpec | None = None,
        output_dir: Path | None = None,
    ) -> IcebergBundle:
        """Emit per-table vN.metadata.json and schema_ops/wave_N_update_schema.json."""
        aliases = assign_physical_aliases(ir, "iceberg", locks=locks)
        planned_hubs, excluded_links, excluded_sats = planned_exclusion_sets(ir)
        wave = ir.wave_number
        files: dict[str, str] = {}

        entities: list[tuple[str, str, list[IRField], list[str]]] = []
        for b in ir.bronze_tables:
            entities.append((ZONE_SCHEMAS["Z0"], b.table_name, b.columns, []))
        for h in ir.hubs:
            if h.hub_name not in planned_hubs:
                hk = h.columns[0].name if h.columns else "hk"
                entities.append((ZONE_SCHEMAS[h.zone_id], h.hub_name, h.columns, [hk]))
        for lk in ir.links:
            if lk.link_name not in excluded_links:
                hk = lk.columns[0].name if lk.columns else "hk"
                entities.append((ZONE_SCHEMAS[lk.zone_id], lk.link_name, lk.columns, [hk]))
        for sat in ir.satellites:
            if sat.sat_name not in excluded_sats:
                hk = sat.columns[0].name if sat.columns else "parent_hk"
                entities.append((ZONE_SCHEMAS[sat.zone_id], sat.sat_name, sat.columns, [hk]))

        prior_cols_map: dict[str, dict[str, IRField]] = {}
        if prior_ir is not None:
            for pb in prior_ir.bronze_tables:
                prior_cols_map[pb.table_name] = {c.name: c for c in pb.columns}
            for ph in prior_ir.hubs:
                prior_cols_map[ph.hub_name] = {c.name: c for c in ph.columns}
            for pl in prior_ir.links:
                prior_cols_map[pl.link_name] = {c.name: c for c in pl.columns}
            for ps in prior_ir.satellites:
                prior_cols_map[ps.sat_name] = {c.name: c for c in ps.columns}

        add_columns: list[dict[str, Any]] = []
        widen_types: list[dict[str, Any]] = []
        global_last_id = 0

        for zone_dir, logical_name, cols, sort_cols in entities:
            phys_name = aliases.get(logical_name, logical_name)
            fields_json, last_col_id = _build_iceberg_fields(cols)
            global_last_id = max(global_last_id, last_col_id)
            table_uuid = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"opendv:{ir.project_id}:{zone_dir}:{logical_name}")
            )
            meta = {
                "format-version": 2,
                "table-uuid": table_uuid,
                "location": f"iceberg/{zone_dir}/{phys_name}",
                "last-sequence-number": wave,
                "last-updated-ms": wave * 1_000_000,
                "last-column-id": last_col_id,
                "current-schema-id": wave,
                "schemas": [
                    {
                        "type": "struct",
                        "schema-id": wave,
                        "fields": fields_json,
                    }
                ],
                "default-spec-id": 0,
                "partition-specs": [{"spec-id": 0, "fields": []}],
                "last-partition-id": 999,
                "default-sort-order-id": 0,
                "sort-orders": [
                    {
                        "order-id": 0,
                        "fields": [
                            {
                                "transform": "identity",
                                "source-id": next((c.field_id for c in cols if c.name == sc), 1),
                                "direction": "asc",
                                "null-order": "nulls-last",
                            }
                            for sc in sort_cols
                            if cols
                        ],
                    }
                ],
                "properties": {"opendv.wave": str(wave), "opendv.zone": zone_dir},
            }
            rel_meta = f"{zone_dir}/{phys_name}/metadata/v{wave}.metadata.json"
            files[rel_meta] = json.dumps(meta, indent=2, sort_keys=True) + "\n"

            old_cols = prior_cols_map.get(logical_name, {})
            for col in cols:
                if logical_name not in prior_cols_map or col.name not in old_cols:
                    if prior_ir is not None:
                        add_columns.append(
                            {
                                "table": f"{zone_dir}.{phys_name}",
                                "field_id": col.field_id,
                                "name": col.name,
                                "type": lower_logical_type(col.logical_type, "iceberg"),
                            }
                        )
                elif col.logical_type != old_cols[col.name].logical_type:
                    widen_types.append(
                        {
                            "table": f"{zone_dir}.{phys_name}",
                            "field_id": col.field_id,
                            "name": col.name,
                            "from_type": lower_logical_type(
                                old_cols[col.name].logical_type, "iceberg"
                            ),
                            "to_type": lower_logical_type(col.logical_type, "iceberg"),
                        }
                    )

        ops_payload = {
            "wave_number": wave,
            "add_columns": add_columns,
            "widen_types": widen_types,
            "last_column_id": global_last_id,
        }
        files[f"schema_ops/wave_{wave}_update_schema.json"] = (
            json.dumps(ops_payload, indent=2, sort_keys=True) + "\n"
        )

        manifest = TargetManifest(
            target_id="iceberg",
            last_compiled_wave=wave,
            last_materialized_wave=None,
            verification_tier="SYNTAX_AND_CONTRACT_CHECKED",
            certification_status="CERTIFIED",
            physical_aliases=aliases,
            eval_receipts=["EVAL-FMT-01"],
            generated_files=sorted([*files.keys(), "target_manifest.json"]),
        )
        files["target_manifest.json"] = manifest.model_dump_json(indent=2) + "\n"

        if output_dir is not None:
            for rel, content in sorted(files.items()):
                dest = output_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8", newline="\n")

        return IcebergBundle(files=files, manifest=manifest)


__all__ = ["IcebergBundle", "IcebergEmitter"]
