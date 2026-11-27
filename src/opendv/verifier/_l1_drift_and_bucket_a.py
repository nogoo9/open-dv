"""Alg A.9a schema drift codemod and portable type/similarity helpers.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from collections.abc import Sequence

from opendv.models.project import EvolutionLockSpec
from opendv.models.sources import ColumnProfile, SourceProfile
from opendv.models.vault_ir import (
    DecimalType,
    DraftField,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
    PortableLogicalType,
    map_sql_to_portable_type,
)
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.verifier._l1_alg_a1 import _is_pii_field


def _levenshtein_similarity(left: str, right: str) -> float:
    a = left.lower().strip()
    b = right.lower().strip()
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    la, lb = len(a), len(b)
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = curr
    return 1.0 - (prev[lb] / max(la, lb))


def _token_dice_similarity(left: str, right: str) -> float:
    ta = {t for t in left.lower().split("_") if t}
    tb = {t for t in right.lower().split("_") if t}
    if not ta or not tb:
        return 0.0
    return (2.0 * len(ta & tb)) / (len(ta) + len(tb))


def column_name_similarity(left: str, right: str) -> float:
    """Self-contained column rename similarity combining Levenshtein and token Dice."""
    return max(_levenshtein_similarity(left, right), _token_dice_similarity(left, right))


def _portable_to_sql_hint(ltype: PortableLogicalType) -> str:
    if isinstance(ltype, DecimalType):
        return f"DECIMAL({ltype.precision},{ltype.scale})"
    if isinstance(ltype, str):
        mapping = {
            "FIXED_BINARY_32": "BINARY(32)",
            "UTF8_STRING": "VARCHAR",
            "INT32": "INTEGER",
            "INT64": "BIGINT",
            "DATE32": "DATE",
            "TIMESTAMP_UTC_US": "TIMESTAMP",
            "BOOLEAN": "BOOLEAN",
            "JSON_DOCUMENT": "JSON",
        }
        return mapping.get(ltype, "VARCHAR")
    return "VARCHAR"


def is_lossless_type_widening(
    old_type: PortableLogicalType,
    new_type: PortableLogicalType,
) -> bool:
    """Return True iff new_type losslessly widens old_type per EVO-04."""
    if old_type == new_type:
        return True
    if old_type == "INT32" and new_type == "INT64":
        return True
    if old_type == "DATE32" and new_type == "TIMESTAMP_UTC_US":
        return True
    if old_type in ("INT32", "INT64") and isinstance(new_type, DecimalType):
        int_digits = new_type.precision - new_type.scale
        required = 10 if old_type == "INT32" else 19
        return int_digits >= required
    if isinstance(old_type, DecimalType) and isinstance(new_type, DecimalType):
        return (new_type.precision - new_type.scale) >= (
            old_type.precision - old_type.scale
        ) and new_type.scale >= old_type.scale
    return False


def next_companion_epoch(
    base_sat_name: str,
    locks: EvolutionLockSpec | None,
    satellites: Sequence[DraftSatelliteSpec],
) -> int:
    """Compute strictly monotonic next companion epoch (> sat_epochs)."""
    epoch = locks.sat_epochs.get(base_sat_name, 1) if locks else 1
    for s in satellites:
        if s.extends_sat_name == base_sat_name and s.companion_epoch is not None:
            epoch = max(epoch, s.companion_epoch)
    return epoch + 1


def apply_alg_a9a_schema_drift(
    draft: DraftVaultIR,
    locks: EvolutionLockSpec | None,
    *,
    prior_ir: CertifiedVaultIR | None = None,
    profiles: Sequence[SourceProfile] = (),
) -> tuple[DraftVaultIR, dict[str, dict[str, str]]]:
    """Alg A.9a: companion sat_*_ext_vN (EVO-01), soft-null (EVO-02), Z1 rename (EVO-03)."""
    if locks is None:
        return draft, {}

    profile_cols_by_table: dict[str, dict[str, ColumnProfile]] = {}
    for prof in profiles:
        for tbl in prof.tables:
            profile_cols_by_table[tbl.table_id] = {c.col_name: c for c in tbl.columns}

    prior_sats = {s.sat_name: s for s in prior_ir.satellites} if prior_ir else {}
    prior_types_by_table: dict[str, dict[str, PortableLogicalType]] = {}
    prior_col_types: dict[str, PortableLogicalType] = {}
    if prior_ir is not None:
        for b in prior_ir.bronze_tables:
            tmap = prior_types_by_table.setdefault(b.source_table_id, {})
            for bfld in b.columns:
                tmap[bfld.name] = bfld.logical_type
                prior_col_types.setdefault(bfld.name, bfld.logical_type)
        for ps in prior_ir.satellites:
            tids = {ps.source_table_id, *(sb.table_id for sb in ps.source_bindings)}
            for a in ps.attributes:
                prior_col_types[a.name] = a.logical_type
                for tid in tids:
                    if tid:
                        prior_types_by_table.setdefault(tid, {})[a.name] = a.logical_type
    z1_overrides: dict[str, dict[str, str]] = {}

    renamed_new_cols: dict[str, set[str]] = {}
    for tbl_id, locked_cols in locks.locked_source_columns.items():
        if tbl_id not in profile_cols_by_table:
            continue
        cur_cols = profile_cols_by_table[tbl_id]
        missing = [c for c in locked_cols if c not in cur_cols]
        if not missing:
            continue
        locked_set = set(locked_cols)
        unmatched_new = [c for c in cur_cols if c not in locked_set]
        tbl_overrides = z1_overrides.setdefault(tbl_id, {})
        tbl_prior_types = prior_types_by_table.get(tbl_id, {})
        for old_col in missing:
            best_match: str | None = None
            best_sim = 0.0
            for cand in unmatched_new:
                sim = column_name_similarity(old_col, cand)
                if sim > best_sim:
                    best_sim = sim
                    best_match = cand
            if best_match is not None and best_sim >= 0.85:
                tbl_overrides[old_col] = f"{best_match} AS {old_col}"
                unmatched_new.remove(best_match)
                renamed_new_cols.setdefault(tbl_id, set()).add(best_match)
            else:
                old_type = tbl_prior_types.get(old_col) or prior_col_types.get(old_col)
                hint = _portable_to_sql_hint(old_type) if old_type is not None else "VARCHAR"
                tbl_overrides[old_col] = f"CAST(NULL AS {hint}) AS {old_col}"

    updated_sats: list[DraftSatelliteSpec] = []
    companion_sats: list[DraftSatelliteSpec] = []
    existing_ext_names = {s.sat_name for s in draft.satellites}

    for sat in draft.satellites:
        s_name = sat.sat_name
        is_retained_ext = sat.extends_sat_name is not None and (
            s_name in prior_sats or s_name in locks.locked_sat_hash_diffs
        )
        if sat.extends_sat_name is not None and not is_retained_ext:
            min_epoch = locks.sat_epochs.get(sat.extends_sat_name, 1) + 1
            if sat.companion_epoch is None or sat.companion_epoch < min_epoch:
                updated_sats.append(sat.model_copy(update={"companion_epoch": min_epoch}))
            else:
                updated_sats.append(sat)
            continue

        if s_name not in locks.locked_sat_hash_diffs:
            updated_sats.append(sat)
            continue

        locked_cols_list = locks.locked_sat_hash_diffs[s_name]
        locked_set = set(locked_cols_list)
        tbl_id = sat.source_bindings[0].table_id if sat.source_bindings else ""
        src_maps = sat.source_bindings[0].col_mappings if sat.source_bindings else {}
        renamed_here = renamed_new_cols.get(tbl_id, set())

        prior_sat = prior_sats.get(s_name)
        prior_attr_map = {a.name: a for a in prior_sat.attributes} if prior_sat else {}
        cand_attr_map = {a.name: a for a in sat.attributes}
        is_mas = sat.sat_kind == "MAS" or bool(sat.child_dependent_keys)
        cdk_set = set(sat.child_dependent_keys)

        preserved_attrs: list[DraftField] = []
        added_attrs: list[DraftField] = []
        incompatible_attrs: list[DraftField] = []

        for col_name in locked_cols_list:
            if col_name in cand_attr_map:
                cand_f = cand_attr_map[col_name]
                old_f = prior_attr_map.get(col_name)
                safe_cand = (
                    cand_f.model_copy(update={"nullable": True})
                    if old_f is not None and old_f.nullable and not cand_f.nullable
                    else cand_f
                )
                if col_name in cdk_set:
                    safe_cand = safe_cand.model_copy(update={"nullable": False})
                if old_f is not None and old_f.logical_type != cand_f.logical_type:
                    if is_lossless_type_widening(old_f.logical_type, cand_f.logical_type):
                        preserved_attrs.append(safe_cand)
                    else:
                        preserved_attrs.append(old_f)
                        incompatible_attrs.append(
                            cand_f.model_copy(update={"nullable": col_name not in cdk_set})
                        )
                else:
                    preserved_attrs.append(safe_cand)
            elif col_name in prior_attr_map:
                old_f = prior_attr_map[col_name]
                preserved_attrs.append(old_f)
                missing_in_prof = (
                    tbl_id
                    and tbl_id in profile_cols_by_table
                    and col_name not in profile_cols_by_table[tbl_id]
                )
                if missing_in_prof:
                    tbl_ov = z1_overrides.setdefault(tbl_id, {})
                    if col_name not in tbl_ov:
                        hint = _portable_to_sql_hint(old_f.logical_type)
                        tbl_ov[col_name] = f"CAST(NULL AS {hint}) AS {col_name}"
            else:
                preserved_attrs.append(
                    DraftField(
                        name=col_name,
                        logical_type="UTF8_STRING",
                        nullable=col_name not in cdk_set,
                        is_pii=False,
                    )
                )

        for fld in sat.attributes:
            if (
                fld.name not in locked_set
                and fld.name not in renamed_here
                and fld.name not in cdk_set
            ):
                added_attrs.append(fld.model_copy(update={"nullable": True}))

        updated_sats.append(sat.model_copy(update={"attributes": preserved_attrs}))

        ext_payload = [*added_attrs, *incompatible_attrs]
        if ext_payload:
            cdk_fields: list[DraftField] = []
            if is_mas:
                pres_map = {f.name: f for f in preserved_attrs}
                for cdk_name in sat.child_dependent_keys:
                    base_cdk = (
                        pres_map.get(cdk_name)
                        or prior_attr_map.get(cdk_name)
                        or cand_attr_map.get(cdk_name)
                        or DraftField(
                            name=cdk_name,
                            logical_type="UTF8_STRING",
                            nullable=False,
                            is_pii=False,
                        )
                    )
                    cdk_fields.append(base_cdk.model_copy(update={"nullable": False}))

            tbl_cprofs = profile_cols_by_table.get(tbl_id, {})
            pii_payload = [
                f.model_copy(
                    update={"is_pii": True, "nullable": f.name not in cdk_set}
                )
                for f in ext_payload
                if _is_pii_field(f, tbl_cprofs.get(f.name))
            ]
            non_pii_payload = [
                f for f in ext_payload if not _is_pii_field(f, tbl_cprofs.get(f.name))
            ]
            if pii_payload:
                base_pii = s_name if s_name.endswith("_pii") else f"{s_name}_pii"
                pii_name = base_pii
                ext_parent: str | None = None
                comp_ep: int | None = None
                if pii_name in locks.locked_sat_hash_diffs or pii_name in existing_ext_names:
                    p_epoch = next_companion_epoch(
                        base_pii, locks, [*draft.satellites, *companion_sats]
                    )
                    pii_name = f"{base_pii}_v{p_epoch}"
                    while pii_name in existing_ext_names:
                        p_epoch += 1
                        pii_name = f"{base_pii}_v{p_epoch}"
                    ext_parent = base_pii if base_pii in existing_ext_names else s_name
                    comp_ep = p_epoch
                existing_ext_names.add(pii_name)
                pii_names_set = {f.name for f in pii_payload}
                pii_attrs = (
                    [*[cf for cf in cdk_fields if cf.name not in pii_names_set], *pii_payload]
                    if is_mas
                    else pii_payload
                )
                pii_maps = {f.name: src_maps.get(f.name, f.name) for f in pii_attrs}
                pii_bindings = [
                    DraftSourceBinding(
                        source_id=b.source_id,
                        table_id=b.table_id,
                        col_mappings=pii_maps,
                    )
                    for b in sat.source_bindings[:1]
                ]
                companion_sats.append(
                    DraftSatelliteSpec(
                        sat_name=pii_name,
                        sat_kind="PII",
                        parent_name=sat.parent_name,
                        source_bindings=pii_bindings,
                        extends_sat_name=ext_parent,
                        companion_epoch=comp_ep,
                        is_bitemporal=sat.is_bitemporal,
                        applied_dts_col=sat.applied_dts_col,
                        load_mode=sat.load_mode,
                        child_dependent_keys=list(sat.child_dependent_keys) if is_mas else [],
                        attributes=pii_attrs,
                    )
                )
            if non_pii_payload:
                base_ext = (
                    sat.extends_sat_name
                    if sat.extends_sat_name is not None
                    else s_name
                )
                next_epoch = next_companion_epoch(
                    base_ext, locks, [*draft.satellites, *companion_sats]
                )
                ext_name = f"{base_ext}_ext_v{next_epoch}"
                while ext_name in existing_ext_names:
                    next_epoch += 1
                    ext_name = f"{base_ext}_ext_v{next_epoch}"
                existing_ext_names.add(ext_name)
                non_pii_names = {f.name for f in non_pii_payload}
                ext_attrs = (
                    [*[cf for cf in cdk_fields if cf.name not in non_pii_names], *non_pii_payload]
                    if is_mas
                    else non_pii_payload
                )
                ext_maps = {f.name: src_maps.get(f.name, f.name) for f in ext_attrs}
                ext_bindings = [
                    DraftSourceBinding(
                        source_id=b.source_id,
                        table_id=b.table_id,
                        col_mappings=ext_maps,
                    )
                    for b in sat.source_bindings[:1]
                ]
                companion_sats.append(
                    DraftSatelliteSpec(
                        sat_name=ext_name,
                        sat_kind="MAS" if is_mas else "EXT",
                        parent_name=sat.parent_name,
                        source_bindings=ext_bindings,
                        extends_sat_name=base_ext,
                        companion_epoch=next_epoch,
                        is_bitemporal=sat.is_bitemporal,
                        applied_dts_col=sat.applied_dts_col,
                        load_mode=sat.load_mode,
                        child_dependent_keys=list(sat.child_dependent_keys) if is_mas else [],
                        attributes=ext_attrs,
                    )
                )

    return draft.model_copy(update={"satellites": [*updated_sats, *companion_sats]}), z1_overrides


__all__ = [
    "apply_alg_a9a_schema_drift",
    "column_name_similarity",
    "is_lossless_type_widening",
    "map_sql_to_portable_type",
    "next_companion_epoch",
]
