"""Pure L1 deterministic codemods: Alg A.1 (3-axis Sat split + YAGNI) & shared options.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from opendv.models.pii_tokenizer import MASKED_PII_TOKEN, PII_TOKEN_RE, RE_PII_COLUMN
from opendv.models.project import EvolutionLockSpec
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
from opendv.models.vault_ir import (
    ColumnTriageEntry,
    DraftField,
    DraftSatelliteSpec,
    DraftVaultIR,
)

_HIGH_FREQ_HINT_RE_PREFIXES: tuple[str, ...] = ("daily_", "intraday_", "realtime_", "live_")
_STATIC_HINT_RE_SUFFIXES: tuple[str, ...] = ("_date", "_code")
_STATIC_HINT_SUBSTRS: tuple[str, ...] = ("_country", "_tier", "_cycle", "_family")
_BYTES_PER_MB = 1024 * 1024


@dataclass(frozen=True)
class L1CodemodOptions:
    """Configurable parameters for L1 deterministic codemods."""

    max_enum_cardinality: int = 50
    mandatory_fk_non_null_ratio: float = 0.95
    yagni_min_general_cols: int = 10
    yagni_min_log_span: float = 2.0
    yagni_min_write_amp_bytes: int = 50 * _BYTES_PER_MB
    high_freq_lambda: float = 0.05
    static_lambda: float = 0.001
    lambda_epsilon: float = 1e-6
    allow_transactional_links: bool = True
    esat_mode: Literal["auto_when_turnover_known", "always_on_driving_hub", "off"] = (
        "auto_when_turnover_known"
    )
    esat_turnover_threshold: float = 0.05
    multi_source_hub_strategy: Literal[
        "unified_canonical_when_same_arity", "companion_hub_with_sal"
    ] = "unified_canonical_when_same_arity"
    waived_ap007_entities: frozenset[str] = frozenset()
    waived_ap008_entities: frozenset[str] = frozenset()


def build_profile_indexes(
    profiles: Sequence[SourceProfile],
) -> tuple[dict[tuple[str, str], ColumnProfile], dict[str, TableProfile]]:
    """Index ColumnProfile by (table_id, col_name) and TableProfile by table_id."""
    col_idx: dict[tuple[str, str], ColumnProfile] = {}
    tbl_idx: dict[str, TableProfile] = {}
    for prof in profiles:
        for tbl in prof.tables:
            tbl_idx[tbl.table_id] = tbl
            for col in tbl.columns:
                col_idx[(tbl.table_id, col.col_name)] = col
    return col_idx, tbl_idx


def _is_pii_field(field: DraftField, col_prof: ColumnProfile | None) -> bool:
    if field.is_pii:
        return True
    if col_prof is not None:
        if col_prof.pii_tags:
            return True
        if any(
            val == MASKED_PII_TOKEN
            or (isinstance(val, str) and bool(PII_TOKEN_RE.fullmatch(val)))
            for val in col_prof.sample_values
        ):
            return True
    return bool(RE_PII_COLUMN.search(field.name))


def _estimate_avg_width(field: DraftField, col_prof: ColumnProfile | None) -> float:
    if col_prof is not None and col_prof.sample_values:
        lengths = [len(v.encode("utf-8")) for v in col_prof.sample_values if v is not None]
        if lengths:
            return max(8.0, float(sum(lengths)) / len(lengths))
    ltype = field.logical_type
    if isinstance(ltype, str):
        if ltype in ("INT32", "DATE32", "BOOLEAN"):
            return 4.0
        if ltype in ("INT64", "TIMESTAMP_UTC_US"):
            return 8.0
    return 32.0


def _infer_lambda(
    field: DraftField,
    table_id: str,
    cdc_volatility: Mapping[str, Mapping[str, float]] | None,
) -> float:
    if cdc_volatility and table_id in cdc_volatility:
        tmap = cdc_volatility[table_id]
        if field.name in tmap:
            return max(0.0, float(tmap[field.name]))
    if field.classification_tag:
        tag = field.classification_tag.upper()
        if tag.startswith("LAMBDA:"):
            raw = tag.split(":", 1)[1].strip()
            try:
                return max(0.0, float(raw))
            except ValueError:
                pass
        if tag == "HIGH_FREQ":
            return 0.5
        if tag == "STATIC":
            return 0.0005
    low_name = field.name.lower()
    if low_name.startswith(_HIGH_FREQ_HINT_RE_PREFIXES):
        return 0.5
    if low_name.endswith(_STATIC_HINT_RE_SUFFIXES) or any(
        sub in low_name for sub in _STATIC_HINT_SUBSTRS
    ):
        return 0.0005
    return 0.01


def _remap_triage(
    triage: list[ColumnTriageEntry],
    remap: Mapping[tuple[str, str], str],
) -> list[ColumnTriageEntry]:
    if not remap:
        return triage
    out: list[ColumnTriageEntry] = []
    for entry in triage:
        key = (entry.table_id, entry.col_name)
        if key in remap and entry.disposition == "MODELED":
            out.append(entry.model_copy(update={"target_ref": remap[key]}))
        else:
            out.append(entry)
    return out


def _split_single_sat_3axis(
    sat: DraftSatelliteSpec,
    col_idx: Mapping[tuple[str, str], ColumnProfile],
    tbl_idx: Mapping[str, TableProfile],
    opts: L1CodemodOptions,
    cdc_volatility: Mapping[str, Mapping[str, float]] | None,
    entity_counts: Mapping[str, int] | None,
) -> tuple[list[DraftSatelliteSpec], dict[tuple[str, str], str]]:
    binding = sat.source_bindings[0] if sat.source_bindings else None
    tbl_id = binding.table_id if binding else ""
    remap: dict[tuple[str, str], str] = {}

    pii_fields: list[DraftField] = []
    general_fields: list[DraftField] = []
    for fld in sorted(sat.attributes, key=lambda f: f.name):
        cprof = col_idx.get((tbl_id, fld.name))
        if _is_pii_field(fld, cprof):
            pii_fields.append(fld.model_copy(update={"is_pii": True}))
        else:
            general_fields.append(fld)

    results: list[DraftSatelliteSpec] = []
    if pii_fields:
        pii_name = sat.sat_name if sat.sat_name.endswith("_pii") else f"{sat.sat_name}_pii"
        results.append(
            sat.model_copy(
                update={
                    "sat_name": pii_name,
                    "sat_kind": "PII",
                    "attributes": pii_fields,
                }
            )
        )
        for fld in pii_fields:
            remap[(tbl_id, fld.name)] = pii_name

    if not general_fields:
        return results, remap

    tbl_prof = tbl_idx.get(tbl_id)
    n_entities = max(1, tbl_prof.row_count_est if tbl_prof else 1)
    if entity_counts and tbl_id in entity_counts:
        n_entities = max(n_entities, entity_counts[tbl_id])

    lambdas = {f.name: _infer_lambda(f, tbl_id, cdc_volatility) for f in general_fields}
    log_vals = [math.log10(v + opts.lambda_epsilon) for v in lambdas.values()]
    log_span = (max(log_vals) - min(log_vals)) if len(log_vals) > 1 else 0.0
    max_lam = max(lambdas.values()) if lambdas else 0.0
    slow_bytes = sum(
        _estimate_avg_width(f, col_idx.get((tbl_id, f.name)))
        for f in general_fields
        if lambdas[f.name] <= opts.high_freq_lambda
    )
    write_amp = n_entities * max_lam * slow_bytes

    should_split = (
        len(general_fields) >= opts.yagni_min_general_cols
        and log_span >= opts.yagni_min_log_span
        and write_amp >= opts.yagni_min_write_amp_bytes
    )
    if not should_split:
        results.append(
            sat.model_copy(update={"sat_kind": "STANDARD", "attributes": general_fields})
        )
        for fld in general_fields:
            remap[(tbl_id, fld.name)] = sat.sat_name
        return results, remap

    buckets: dict[str, list[DraftField]] = {"static": [], "core": [], "high_freq": []}
    for fld in general_fields:
        lam = lambdas[fld.name]
        if lam <= opts.static_lambda:
            buckets["static"].append(fld)
        elif lam <= opts.high_freq_lambda:
            buckets["core"].append(fld)
        else:
            buckets["high_freq"].append(fld)

    for suffix, fields in buckets.items():
        if not fields:
            continue
        sub_name = f"{sat.sat_name}_{suffix}"
        results.append(
            sat.model_copy(
                update={"sat_name": sub_name, "sat_kind": "STANDARD", "attributes": fields}
            )
        )
        for fld in fields:
            remap[(tbl_id, fld.name)] = sub_name
    return results, remap


def _unique_sat_name(base: str, used_names: set[str]) -> str:
    if base not in used_names:
        used_names.add(base)
        return base
    idx = 2
    while f"{base}_{idx}" in used_names:
        idx += 1
    cand = f"{base}_{idx}"
    used_names.add(cand)
    return cand


def apply_alg_a1_satellite_split(
    draft: DraftVaultIR,
    profiles: Sequence[SourceProfile],
    locks: EvolutionLockSpec | None = None,
    *,
    options: L1CodemodOptions | None = None,
    cdc_volatility_by_table: Mapping[str, Mapping[str, float]] | None = None,
    entity_count_overrides: Mapping[str, int] | None = None,
    protected_sat_names: Collection[str] = (),
) -> DraftVaultIR:
    """Alg A.1: 3-axis source/PII/velocity Satellite split with 50 MB/day YAGNI guard."""
    opts = options or L1CodemodOptions()
    col_idx, tbl_idx = build_profile_indexes(profiles)
    locked_sats = locks.locked_sat_hash_diffs if locks else {}
    protected_set = set(protected_sat_names)

    out_sats: list[DraftSatelliteSpec] = []
    remap: dict[tuple[str, str], str] = {}
    used_names: set[str] = {s.sat_name for s in draft.satellites}

    for sat in draft.satellites:
        if sat.sat_kind != "STANDARD" or sat.sat_name in locked_sats:
            out_sats.append(sat)
            continue

        if len(sat.source_bindings) > 1:
            used_names.discard(sat.sat_name)
            src_counts: dict[str, int] = {}
            for sb in sat.source_bindings:
                src_counts[sb.source_id] = src_counts.get(sb.source_id, 0) + 1
            for idx, sb in enumerate(sat.source_bindings):
                mapped = set(sb.col_mappings.values()) | set(sb.col_mappings.keys())
                sub_attrs = [f for f in sat.attributes if f.name in mapped] or list(
                    sat.attributes
                )
                tbl_slug = sb.table_id.split(":", 1)[-1].split(".")[-1]
                if src_counts.get(sb.source_id, 0) > 1:
                    suffix = f"{sb.source_id}_{tbl_slug}" if sb.source_id else tbl_slug
                else:
                    suffix = sb.source_id or tbl_slug or f"s{idx}"
                cand_name = _unique_sat_name(f"{sat.sat_name}_{suffix}", used_names)
                cand = sat.model_copy(
                    update={
                        "sat_name": cand_name,
                        "source_bindings": [sb],
                        "attributes": sub_attrs,
                    }
                )
                split_items, item_remap = _split_single_sat_3axis(
                    cand, col_idx, tbl_idx, opts, cdc_volatility_by_table, entity_count_overrides
                )
                for item in split_items:
                    if item.sat_name != cand_name:
                        uniq = _unique_sat_name(item.sat_name, used_names)
                        if uniq != item.sat_name:
                            for k, v in list(item_remap.items()):
                                if v == item.sat_name:
                                    item_remap[k] = uniq
                            item = item.model_copy(update={"sat_name": uniq})
                    out_sats.append(item)
                remap.update(item_remap)
        else:
            split_items, item_remap = _split_single_sat_3axis(
                sat, col_idx, tbl_idx, opts, cdc_volatility_by_table, entity_count_overrides
            )
            for item in split_items:
                if item.sat_name != sat.sat_name:
                    uniq = _unique_sat_name(item.sat_name, used_names)
                    if uniq != item.sat_name:
                        for k, v in list(item_remap.items()):
                            if v == item.sat_name:
                                item_remap[k] = uniq
                        item = item.model_copy(update={"sat_name": uniq})
                else:
                    used_names.add(item.sat_name)
                out_sats.append(item)
            remap.update(item_remap)

    # Wave-1 micro-satellite merge on unlocked STANDARD satellites on same (parent, src, tbl, temp)
    if draft.wave_number == 1:
        grouped: dict[tuple[str, str, str, bool, str], list[DraftSatelliteSpec]] = {}
        passthrough: list[DraftSatelliteSpec] = []
        for sat in out_sats:
            is_velocity_split = sat.sat_name in protected_set or sat.sat_name.endswith(
                ("_high_freq", "_static", "_core")
            )
            if (
                sat.sat_kind == "STANDARD"
                and sat.sat_name not in locked_sats
                and not is_velocity_split
                and len(sat.source_bindings) == 1
                and len(sat.attributes) <= 2
            ):
                sb = sat.source_bindings[0]
                gkey = (
                    sat.parent_name,
                    sb.source_id,
                    sb.table_id,
                    sat.is_bitemporal,
                    sat.applied_dts_col or "",
                )
                grouped.setdefault(gkey, []).append(sat)
            else:
                passthrough.append(sat)

        merged_sats: list[DraftSatelliteSpec] = list(passthrough)
        for (_, _, tbl_id, _, _), micro_group in grouped.items():
            if len(micro_group) < 3:
                merged_sats.extend(micro_group)
                continue
            tbl_prof = tbl_idx.get(tbl_id)
            n_ent = max(1, tbl_prof.row_count_est if tbl_prof else 1)
            if entity_count_overrides and tbl_id in entity_count_overrides:
                n_ent = max(n_ent, entity_count_overrides[tbl_id])
            all_fields = [f for s in micro_group for f in s.attributes]
            max_lam = max(
                (_infer_lambda(f, tbl_id, cdc_volatility_by_table) for f in all_fields),
                default=0.0,
            )
            slow_bytes = sum(
                _estimate_avg_width(f, col_idx.get((tbl_id, f.name))) for f in all_fields
            )
            if n_ent * max_lam * slow_bytes >= opts.yagni_min_write_amp_bytes:
                merged_sats.extend(micro_group)
                continue
            base = micro_group[0]
            seen_names: set[str] = set()
            dedup_attrs: list[DraftField] = []
            merged_maps: dict[str, str] = {}
            for s in micro_group:
                merged_maps.update(s.source_bindings[0].col_mappings)
                for f in s.attributes:
                    if f.name not in seen_names:
                        seen_names.add(f.name)
                        dedup_attrs.append(f)
                    remap[(tbl_id, f.name)] = base.sat_name
            merged_binding = base.source_bindings[0].model_copy(
                update={"col_mappings": merged_maps}
            )
            merged_sats.append(
                base.model_copy(
                    update={"source_bindings": [merged_binding], "attributes": dedup_attrs}
                )
            )
        out_sats = merged_sats

    def _order_extensions_ahead(sats: list[DraftSatelliteSpec]) -> list[DraftSatelliteSpec]:
        groups: dict[tuple[str, str, str], list[tuple[int, DraftSatelliteSpec]]] = {}
        for idx, sat in enumerate(sats):
            sb0 = sat.source_bindings[0] if sat.source_bindings else None
            gkey = (sat.parent_name, sb0.source_id if sb0 else "", sb0.table_id if sb0 else "")
            groups.setdefault(gkey, []).append((idx, sat))
        ordered = list(sats)
        for items in groups.values():
            if len(items) > 1 and any(s.extends_sat_name is not None for _, s in items):
                slots = [idx for idx, _ in items]
                sorted_sats = [
                    s
                    for _, s in sorted(
                        items,
                        key=lambda pair: (
                            -(pair[1].companion_epoch or 1),
                            0 if pair[1].extends_sat_name is not None else 1,
                            pair[0],
                        ),
                    )
                ]
                for slot, s in zip(slots, sorted_sats, strict=True):
                    ordered[slot] = s
        return ordered

    out_sats = _order_extensions_ahead(out_sats)
    updated_triage = _remap_triage(draft.column_triage, remap)
    return draft.model_copy(update={"satellites": out_sats, "column_triage": updated_triage})


__all__ = [
    "L1CodemodOptions",
    "apply_alg_a1_satellite_split",
    "build_profile_indexes",
]
