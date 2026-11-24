"""Gate V5: EvolutionLockSpec parity (DV-EVO-001..008), cold-start guard, and IR DV-AP-010.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

import re

from opendv.models.diagnostics import VerifierDiagnostic
from opendv.models.project import EvolutionLockSpec
from opendv.models.vault_ir import DraftField
from opendv.models.vault_ir_certified import CertifiedVaultIR, IRField
from opendv.verifier._l1_drift_and_bucket_a import is_lossless_type_widening

_FORBIDDEN_Z1_EXPR_RE = re.compile(
    r"(?i)(?:\bJOIN\b|\bGROUP\s+BY\b|\bHAVING\b|\bWHERE\b|\bCASE\b[\s\S]*?\bWHEN\b|\bUNION\b)"
)


def _diag(
    rule_id: str,
    severity: str,
    entity_name: str,
    message: str,
) -> VerifierDiagnostic:
    sev = severity if severity in ("BLOCKER", "ERROR", "WARN", "ADVISORY") else "ERROR"
    return VerifierDiagnostic(
        gate="V5",
        rule_id=rule_id,
        severity=sev,  # type: ignore[arg-type]
        entity_name=entity_name,
        waivable=False,
        message=message,
    )


def verify_v5_evolution(
    ir: CertifiedVaultIR,
    *,
    locks: EvolutionLockSpec | None = None,
    prior_ir: CertifiedVaultIR | None = None,
    cold_start_requested: bool = False,
) -> list[VerifierDiagnostic]:
    """Run Gate V5 evolution lock checks (DV-EVO-001..008) and IR-only DV-AP-010."""
    diags: list[VerifierDiagnostic] = []

    z2_sat_sigs: dict[tuple[str, str, tuple[str, ...]], str] = {
        (sat.parent_name, sat.source_table_id, tuple(sorted(a.name for a in sat.attributes))): (
            sat.sat_name
        )
        for sat in ir.satellites
        if sat.zone_id == "Z2" and sat.sat_kind == "STANDARD"
    }
    for sat in ir.satellites:
        if sat.zone_id == "Z3" and sat.sat_kind == "STANDARD":
            sig = (
                sat.parent_name,
                sat.source_table_id,
                tuple(sorted(a.name for a in sat.attributes)),
            )
            if sig in z2_sat_sigs:
                diags.append(
                    _diag(
                        "DV-AP-010",
                        "ERROR",
                        sat.sat_name,
                        f"Z3 Satellite {sat.sat_name} mirrors Z2 {z2_sat_sigs[sig]} 1:1.",
                    )
                )

    for stg in ir.staging_views:
        if not stg.source_id or not stg.source_table_id:
            diags.append(
                _diag(
                    "DV-AP-010",
                    "ERROR",
                    stg.view_name,
                    f"StagingViewSpec {stg.view_name} must bind 1 source_id and source_table_id.",
                )
            )
        for target_col, expr in stg.col_lineage_mappings.items():
            if _FORBIDDEN_Z1_EXPR_RE.search(expr):
                diags.append(
                    _diag(
                        "DV-AP-010",
                        "ERROR",
                        stg.view_name,
                        f"Z1 {stg.view_name}.{target_col} leaks soft SQL ({expr!r}).",
                    )
                )

    if locks is None:
        return diags

    has_active_locks = bool(
        locks.locked_hub_bks or locks.locked_links or locks.locked_sat_hash_diffs
    )
    if cold_start_requested and has_active_locks:
        diags.append(
            _diag(
                "DV-EVO-002",
                "BLOCKER",
                "__cold_start__",
                f"__cold_start__ forbidden while Wave {locks.wave_number} locks are active.",
            )
        )

    if (
        ir.seed_records.unknown_hk_hex != "0" * 64
        or ir.seed_records.error_hk_hex != "f" * 64
        or ir.seed_records.ghost_load_dts != "1900-01-01T00:00:00Z"
    ):
        diags.append(
            _diag(
                "DV-EVO-001",
                "BLOCKER",
                "seed_records",
                "SeedRecordsSpec sentinel keys or ghost timestamp mutated across wave.",
            )
        )

    hubs_by_name = {h.hub_name: h for h in ir.hubs}
    links_by_name = {lnk.link_name: lnk for lnk in ir.links}
    sats_by_name = {s.sat_name: s for s in ir.satellites}

    for hub_name, locked_bks in locks.locked_hub_bks.items():
        hub = hubs_by_name.get(hub_name)
        if hub is None:
            diags.append(
                _diag("DV-EVO-002", "BLOCKER", hub_name, f"Locked Hub {hub_name} deleted.")
            )
            continue
        actual_bks = sorted({bk.col_name for bk in hub.business_keys})
        if sorted(set(locked_bks)) != actual_bks:
            diags.append(
                _diag(
                    "DV-EVO-003",
                    "BLOCKER",
                    hub_name,
                    f"Locked Hub {hub_name} BKs mutated: "
                    f"{sorted(set(locked_bks))} != {actual_bks}.",
                )
            )

    for link_name, locked_hubs in locks.locked_links.items():
        lnk = links_by_name.get(link_name)
        if lnk is None:
            diags.append(
                _diag("DV-EVO-002", "BLOCKER", link_name, f"Locked Link {link_name} deleted.")
            )
            continue
        actual_hubs = sorted({p.hub_name for p in lnk.participants})
        if sorted(set(locked_hubs)) != actual_hubs:
            diags.append(
                _diag(
                    "DV-EVO-003",
                    "BLOCKER",
                    link_name,
                    f"Locked Link {link_name} participants mutated: "
                    f"{sorted(set(locked_hubs))} != {actual_hubs}.",
                )
            )

    for sat_name, locked_cols in locks.locked_sat_hash_diffs.items():
        locked_sat = sats_by_name.get(sat_name)
        if locked_sat is None:
            diags.append(
                _diag("DV-EVO-002", "BLOCKER", sat_name, f"Locked Satellite {sat_name} deleted.")
            )
            continue
        actual_cols = sorted(a.name for a in locked_sat.attributes)
        expected_cols = sorted(set(locked_cols))
        missing = set(expected_cols) - set(actual_cols)
        added_in_place = set(actual_cols) - set(expected_cols)
        if missing:
            diags.append(
                _diag(
                    "DV-EVO-004",
                    "BLOCKER",
                    sat_name,
                    f"Locked Satellite {sat_name} dropped columns {sorted(missing)}.",
                )
            )
        if added_in_place:
            diags.append(
                _diag(
                    "DV-EVO-006",
                    "BLOCKER",
                    sat_name,
                    f"Locked Satellite {sat_name} added {sorted(added_in_place)} in-place.",
                )
            )

    prior_ext_sats = (
        {s.sat_name: s for s in prior_ir.satellites if s.sat_kind == "EXT"}
        if prior_ir is not None
        else {}
    )
    for sat in ir.satellites:
        if sat.sat_kind == "EXT":
            if not sat.extends_sat_name or sat.companion_epoch is None:
                diags.append(
                    _diag(
                        "DV-EVO-006",
                        "BLOCKER",
                        sat.sat_name,
                        f"EXT Satellite {sat.sat_name} needs extends_sat_name and companion_epoch.",
                    )
                )
            elif sat.sat_name in prior_ext_sats:
                prior_ep = prior_ext_sats[sat.sat_name].companion_epoch
                if sat.companion_epoch != prior_ep:
                    diags.append(
                        _diag(
                            "DV-EVO-006",
                            "BLOCKER",
                            sat.sat_name,
                            f"Retained EXT {sat.sat_name} mutated epoch {prior_ep} -> "
                            f"{sat.companion_epoch}.",
                        )
                    )
            elif prior_ir is None and sat.sat_name in locks.locked_sat_hash_diffs:
                continue
            else:
                prev_epoch = locks.sat_epochs.get(sat.extends_sat_name, 1)
                if sat.companion_epoch <= prev_epoch:
                    diags.append(
                        _diag(
                            "DV-EVO-006",
                            "BLOCKER",
                            sat.sat_name,
                            f"EXT {sat.sat_name} epoch={sat.companion_epoch} "
                            f"<= prior {prev_epoch}.",
                        )
                    )

    prior_field_ids: dict[str, dict[str, int]] = {}
    prior_attrs: dict[str, dict[str, DraftField]] = {}
    if prior_ir is not None:
        for h in prior_ir.hubs:
            prior_field_ids[h.hub_name] = {c.name: c.field_id for c in h.columns}
        for lnk in prior_ir.links:
            prior_field_ids[lnk.link_name] = {c.name: c.field_id for c in lnk.columns}
        for psat in prior_ir.satellites:
            prior_field_ids[psat.sat_name] = {c.name: c.field_id for c in psat.columns}
            prior_attrs[psat.sat_name] = {a.name: a for a in psat.attributes}

    def _check_entity_hwm(entity_name: str, cols: list[IRField]) -> None:
        hwm = locks.iceberg_high_water_marks.get(entity_name)
        if hwm is None:
            return
        if prior_ir is None:
            if cols and max(f.field_id for f in cols) < hwm:
                diags.append(
                    _diag(
                        "DV-EVO-006",
                        "BLOCKER",
                        entity_name,
                        f"{entity_name} max field_id < HWM={hwm}.",
                    )
                )
            return
        p_ids = prior_field_ids.get(entity_name, {})
        for fld in cols:
            if fld.name not in p_ids and fld.field_id <= hwm:
                diags.append(
                    _diag(
                        "DV-EVO-006",
                        "BLOCKER",
                        entity_name,
                        f"New IRField {entity_name}.{fld.name} "
                        f"field_id={fld.field_id} <= HWM={hwm}.",
                    )
                )
            elif fld.name in p_ids and fld.field_id != p_ids[fld.name]:
                diags.append(
                    _diag(
                        "DV-EVO-006",
                        "BLOCKER",
                        entity_name,
                        f"Locked IRField {entity_name}.{fld.name} changed field_id.",
                    )
                )

    for h in ir.hubs:
        _check_entity_hwm(h.hub_name, h.columns)
    for lnk in ir.links:
        _check_entity_hwm(lnk.link_name, lnk.columns)
    for sat in ir.satellites:
        _check_entity_hwm(sat.sat_name, sat.columns)
        p_attr_map = prior_attrs.get(sat.sat_name, {})
        for attr in sat.attributes:
            old_attr = p_attr_map.get(attr.name)
            if old_attr is None:
                continue
            if old_attr.logical_type != attr.logical_type and not is_lossless_type_widening(
                old_attr.logical_type, attr.logical_type
            ):
                diags.append(
                    _diag(
                        "DV-EVO-007",
                        "BLOCKER",
                        sat.sat_name,
                        f"Column {sat.sat_name}.{attr.name} narrowed/changed type.",
                    )
                )
            if old_attr.nullable and not attr.nullable:
                diags.append(
                    _diag(
                        "DV-EVO-008",
                        "BLOCKER",
                        sat.sat_name,
                        f"Column {sat.sat_name}.{attr.name} tightened nullable=True -> False.",
                    )
                )

    return diags


__all__ = ["verify_v5_evolution"]
