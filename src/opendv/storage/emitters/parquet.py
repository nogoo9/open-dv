"""PyArrow ZSTD Parquet bundle emitter (`EVAL-FMT-02`).

Strictly Layer 2a: imports only opendv.models, pyarrow, dialects, ddl_builders, stdlib.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from opendv.models.project import EvolutionLockSpec, TargetManifest
from opendv.models.vault_ir import ArrayType, DecimalType, MapType, PortableLogicalType
from opendv.models.vault_ir_certified import CertifiedVaultIR, IRField, SeedRecordsSpec
from opendv.storage.dialects import assign_physical_aliases, get_dialect_profile, lower_logical_type
from opendv.storage.sql_compiler.ddl_builders import (
    ZONE_SCHEMAS,
    planned_exclusion_sets,
    resolve_pit_bridge_columns,
)


def _to_arrow_type(ltype: PortableLogicalType) -> pa.DataType:
    if isinstance(ltype, DecimalType):
        return pa.decimal128(ltype.precision, ltype.scale)
    if isinstance(ltype, ArrayType):
        return pa.list_(_to_arrow_type(ltype.element_type))
    if isinstance(ltype, MapType):
        return pa.map_(_to_arrow_type(ltype.key_type), _to_arrow_type(ltype.value_type))
    mapping: dict[str, pa.DataType] = {
        "FIXED_BINARY_32": pa.binary(32),
        "UTF8_STRING": pa.string(),
        "INT32": pa.int32(),
        "INT64": pa.int64(),
        "DATE32": pa.date32(),
        "TIMESTAMP_UTC_US": pa.timestamp("us", tz="UTC"),
        "BOOLEAN": pa.bool_(),
        "JSON_DOCUMENT": pa.string(),
    }
    return mapping.get(str(ltype), pa.string())


def _coerce_arrow_value(val: Any, ltype: PortableLogicalType) -> Any:
    if val is None:
        return None
    if ltype == "FIXED_BINARY_32":
        if isinstance(val, (bytes, bytearray, memoryview)):
            b = bytes(val)
            return b[:32].ljust(32, b"\x00")
        s = str(val).removeprefix("0x")
        if len(s) == 64:
            return bytes.fromhex(s)
        return s.encode("utf-8")[:32].ljust(32, b"\x00")
    if ltype == "TIMESTAMP_UTC_US":
        if isinstance(val, datetime):
            return val.replace(tzinfo=UTC) if val.tzinfo is None else val.astimezone(UTC)
        raw = str(val).strip().replace(" ", "T")
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    if ltype == "DATE32":
        if isinstance(val, datetime):
            return val.date()
        if isinstance(val, date):
            return val
        return date.fromisoformat(str(val)[:10])
    if isinstance(ltype, DecimalType):
        return Decimal(str(val))
    if ltype in ("INT32", "INT64"):
        return int(val)
    if ltype == "BOOLEAN":
        if isinstance(val, bool):
            return val
        if isinstance(val, int):
            return bool(val)
        if isinstance(val, str):
            norm = val.strip().lower()
            if norm in ("true", "1", "t", "yes"):
                return True
            if norm in ("false", "0", "f", "no"):
                return False
            raise ValueError(f"Invalid boolean literal: {val!r}")
        return bool(val)
    if isinstance(ltype, ArrayType):
        if isinstance(val, (list, tuple)):
            return [_coerce_arrow_value(el, ltype.element_type) for el in val]
        return []
    if isinstance(ltype, MapType):
        pairs = val.items() if isinstance(val, Mapping) else val
        return [
            (_coerce_arrow_value(k, ltype.key_type), _coerce_arrow_value(v, ltype.value_type))
            for k, v in pairs
        ]
    return str(val) if not isinstance(val, str) else val


def _seed_value(
    col: IRField,
    seed: SeedRecordsSpec,
    *,
    is_error: bool = False,
    parent_hk_name: str = "",
) -> Any:
    if col.name == parent_hk_name or col.name.startswith("hk_"):
        return bytes.fromhex(seed.error_hk_hex if is_error else seed.unknown_hk_hex)
    if col.name == "hash_diff":
        return bytes.fromhex(seed.ghost_hash_diff_hex)
    if col.name == "bkcc":
        return "GLOBAL"
    if col.name == "load_dts" or col.name == "applied_dts":
        return _coerce_arrow_value(seed.ghost_load_dts, "TIMESTAMP_UTC_US")
    if col.name == "rec_src":
        return seed.ghost_rec_src
    if col.nullable:
        return None
    if col.logical_type == "FIXED_BINARY_32":
        return bytes.fromhex(seed.error_hk_hex if is_error else seed.unknown_hk_hex)
    if col.logical_type == "TIMESTAMP_UTC_US":
        return _coerce_arrow_value(seed.ghost_load_dts, "TIMESTAMP_UTC_US")
    if col.logical_type == "DATE32":
        return date(1900, 1, 1)
    if col.logical_type == "BOOLEAN":
        return False
    if col.logical_type in ("INT32", "INT64"):
        return -2 if is_error else seed.default_num
    if isinstance(col.logical_type, DecimalType):
        return Decimal("-2" if is_error else str(seed.default_num))
    if isinstance(col.logical_type, ArrayType):
        return []
    if isinstance(col.logical_type, MapType):
        return []
    return "ERROR" if is_error else seed.default_string


def _build_arrow_table(
    cols: Sequence[IRField],
    rows: Sequence[Mapping[str, Any]],
) -> pa.Table:
    schema = pa.schema(
        [pa.field(c.name, _to_arrow_type(c.logical_type), nullable=c.nullable) for c in cols]
    )
    arrays: list[pa.Array] = []
    for col in cols:
        col_vals = [_coerce_arrow_value(r.get(col.name), col.logical_type) for r in rows]
        arrays.append(pa.array(col_vals, type=_to_arrow_type(col.logical_type)))
    return pa.Table.from_arrays(arrays, schema=schema)


@dataclass(frozen=True, slots=True)
class ParquetBundle:
    """Generated Parquet bundle summary and TargetManifest."""

    files: list[str]
    manifest: TargetManifest


class ParquetEmitter:
    """Emit PyArrow ZSTD Parquet partitions for Z0..Z4 including Zero/Ghost rows."""

    def emit(
        self,
        ir: CertifiedVaultIR,
        output_dir: Path,
        *,
        locks: EvolutionLockSpec | None = None,
        rows_by_entity: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        materialized_wave: int | None = None,
    ) -> ParquetBundle:
        """Write schema_manifest.json, wave=W00N/*.zstd.parquet, and z3_derived/*/latest/."""
        output_dir.mkdir(parents=True, exist_ok=True)
        aliases = assign_physical_aliases(ir, "parquet", locks=locks)
        planned_hubs, excluded_links, excluded_sats = planned_exclusion_sets(ir)
        wave = ir.wave_number
        data_map = rows_by_entity or {}
        written: list[str] = []
        manifest_tables: list[dict[str, Any]] = []

        def _write_table(
            relpath: str, cols: Sequence[IRField], rows: Sequence[Mapping[str, Any]]
        ) -> None:
            dest = output_dir / relpath
            dest.parent.mkdir(parents=True, exist_ok=True)
            tbl = _build_arrow_table(cols, rows)
            pq.write_table(tbl, dest, compression="zstd")
            written.append(relpath)

        # Z0 Bronze
        for b in ir.bronze_tables:
            phys = aliases.get(b.table_name, b.table_name)
            rel = f"{ZONE_SCHEMAS['Z0']}/{phys}/wave=W{wave:03d}/part-00000.zstd.parquet"
            rows = list(data_map.get(b.table_name, ()))
            _write_table(rel, b.columns, rows)
            manifest_tables.append(
                {
                    "entity": b.table_name,
                    "zone": "Z0",
                    "columns": [
                        {"name": c.name, "type": lower_logical_type(c.logical_type, "parquet")}
                        for c in b.columns
                    ],
                }
            )

        # Z2/Z3 Hubs
        for h in ir.hubs:
            if h.hub_name in planned_hubs:
                continue
            phys = aliases.get(h.hub_name, h.hub_name)
            rel = f"{ZONE_SCHEMAS[h.zone_id]}/{phys}/wave=W{wave:03d}/part-00000.zstd.parquet"
            seeds = [
                {c.name: _seed_value(c, ir.seed_records, is_error=False) for c in h.columns},
                {c.name: _seed_value(c, ir.seed_records, is_error=True) for c in h.columns},
            ]
            rows = [*seeds, *data_map.get(h.hub_name, ())]
            _write_table(rel, h.columns, rows)
            manifest_tables.append({"entity": h.hub_name, "zone": h.zone_id})

        # Z2/Z3 Links
        for lk in ir.links:
            if lk.link_name in excluded_links:
                continue
            phys = aliases.get(lk.link_name, lk.link_name)
            rel = f"{ZONE_SCHEMAS[lk.zone_id]}/{phys}/wave=W{wave:03d}/part-00000.zstd.parquet"
            seeds = [
                {c.name: _seed_value(c, ir.seed_records, is_error=False) for c in lk.columns},
                {c.name: _seed_value(c, ir.seed_records, is_error=True) for c in lk.columns},
            ]
            rows = [*seeds, *data_map.get(lk.link_name, ())]
            _write_table(rel, lk.columns, rows)
            manifest_tables.append({"entity": lk.link_name, "zone": lk.zone_id})

        # Z2/Z3 Satellites
        for sat in ir.satellites:
            if sat.sat_name in excluded_sats:
                continue
            phys = aliases.get(sat.sat_name, sat.sat_name)
            rel = f"{ZONE_SCHEMAS[sat.zone_id]}/{phys}/wave=W{wave:03d}/part-00000.zstd.parquet"
            parent_hk = sat.columns[0].name if sat.columns else ""
            ghost = {
                c.name: _seed_value(c, ir.seed_records, is_error=False, parent_hk_name=parent_hk)
                for c in sat.columns
            }
            rows = [ghost, *data_map.get(sat.sat_name, ())]
            _write_table(rel, sat.columns, rows)
            manifest_tables.append({"entity": sat.sat_name, "zone": sat.zone_id})

        # Z3 Derived PIT / Bridge -> z3_derived/<name>/latest/part-00000.zstd.parquet
        max_ident = get_dialect_profile("parquet").max_ident_bytes
        for pb in ir.pit_bridges:
            if pb.root_hub in planned_hubs:
                continue
            phys = aliases.get(pb.object_name, pb.object_name)
            _, root_hk, targets = resolve_pit_bridge_columns(pb, ir, max_ident)
            pb_cols: list[IRField] = [
                IRField(
                    field_id=1,
                    name=root_hk,
                    logical_type="FIXED_BINARY_32",
                    nullable=False,
                    is_pii=False,
                )
            ]
            fid = 2
            if pb.object_kind == "PIT":
                pb_cols.append(
                    IRField(
                        field_id=fid,
                        name="snapshot_dts",
                        logical_type="TIMESTAMP_UTC_US",
                        nullable=False,
                        is_pii=False,
                    )
                )
                fid += 1
                for _, hk_col, ldts_col, app_col, app_ltype in targets:
                    pb_cols.append(
                        IRField(
                            field_id=fid,
                            name=hk_col,
                            logical_type="FIXED_BINARY_32",
                            nullable=False,
                            is_pii=False,
                        )
                    )
                    fid += 1
                    pb_cols.append(
                        IRField(
                            field_id=fid,
                            name=ldts_col,
                            logical_type="TIMESTAMP_UTC_US",
                            nullable=False,
                            is_pii=False,
                        )
                    )
                    fid += 1
                    if app_col is not None:
                        pb_cols.append(
                            IRField(
                                field_id=fid,
                                name=app_col,
                                logical_type=app_ltype,
                                nullable=False,
                                is_pii=False,
                            )
                        )
                        fid += 1
            else:
                for _, fk_col, _, _, _ in targets:
                    pb_cols.append(
                        IRField(
                            field_id=fid,
                            name=fk_col,
                            logical_type="FIXED_BINARY_32",
                            nullable=False,
                            is_pii=False,
                        )
                    )
                    fid += 1
                pb_cols.append(
                    IRField(
                        field_id=fid,
                        name="load_dts",
                        logical_type="TIMESTAMP_UTC_US",
                        nullable=False,
                        is_pii=False,
                    )
                )
            rel = f"z3_derived/{phys}/latest/part-00000.zstd.parquet"
            _write_table(rel, pb_cols, list(data_map.get(pb.object_name, ())))

        schema_manifest = {"wave_number": wave, "tables": manifest_tables}
        (output_dir / "schema_manifest.json").write_text(
            json.dumps(schema_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        written.append("schema_manifest.json")

        manifest = TargetManifest(
            target_id="parquet",
            last_compiled_wave=wave,
            last_materialized_wave=materialized_wave,
            verification_tier="RUNTIME_VERIFIED",
            certification_status="CERTIFIED",
            physical_aliases=aliases,
            eval_receipts=["EVAL-FMT-02"],
            generated_files=sorted([*written, "target_manifest.json"]),
        )
        (output_dir / "target_manifest.json").write_text(
            manifest.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return ParquetBundle(files=sorted(written), manifest=manifest)


__all__ = ["ParquetBundle", "ParquetEmitter"]
