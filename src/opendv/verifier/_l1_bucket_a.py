"""BucketAOperatorCall executor and evolution_locks rerouting / Bucket B escalation.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from collections.abc import Sequence

from opendv.models.diagnostics import HorizonQuestion
from opendv.models.project import EvolutionLockSpec
from opendv.models.vault_ir import (
    DecimalType,
    DraftField,
    DraftHubSpec,
    DraftSatelliteSpec,
    DraftVaultIR,
)
from opendv.models.wire_dtos_vault import BucketAOperatorCall
from opendv.verifier._l1_bucket_a_hub_ops import (
    apply_promote_col_to_hub,
    apply_promote_hub_bk_immutable,
    escalate_question,
)
from opendv.verifier._l1_drift_and_bucket_a import (
    is_lossless_type_widening,
    next_companion_epoch,
)
from opendv.verifier.ports import BucketAExecutionResult


def _apply_set_bkcc(
    draft: DraftVaultIR,
    call: BucketAOperatorCall,
    locks: EvolutionLockSpec | None,
) -> tuple[DraftVaultIR, list[str], list[HorizonQuestion]]:
    bkcc_val = call.params.get("bkcc_value", "").strip()
    bkcc_col = call.params.get("bkcc_col")
    if not call.target_hub or not bkcc_val:
        raise ValueError("SET_BKCC requires target_hub and params['bkcc_value']")
    hub = next((h for h in draft.hubs if h.hub_name == call.target_hub), None)
    matched_bks = [
        bk
        for bk in (hub.business_keys if hub else [])
        if (call.table_id is None or bk.table_id == call.table_id)
        and (call.source_column is None or bk.col_name == call.source_column)
    ]
    is_locked = bool(locks and call.target_hub in locks.locked_hub_bks)
    if hub is None or not matched_bks:
        if is_locked:
            return (
                draft,
                [],
                [
                    escalate_question(
                        call,
                        "BRK-01",
                        f"Unmatched SET_BKCC on locked Hub {call.target_hub}",
                        f"No BusinessKeyPart matched table_id={call.table_id!r} on "
                        f"{call.target_hub}.",
                    )
                ],
            )
        raise ValueError(
            f"SET_BKCC matched no BusinessKeyPart on {call.target_hub} "
            f"(table_id={call.table_id!r})"
        )
    if is_locked:
        existing_vals = {bk.bkcc_value for bk in matched_bks}
        if existing_vals != {bkcc_val.upper()}:
            return (
                draft,
                [],
                [
                    escalate_question(
                        call,
                        "BRK-01",
                        f"Locked Hub BKCC conflict on {call.target_hub}",
                        f"Cannot mutate BKCC on locked Hub {call.target_hub} to {bkcc_val}.",
                    )
                ],
            )
    hubs: list[DraftHubSpec] = []
    for h in draft.hubs:
        if h.hub_name != call.target_hub:
            hubs.append(h)
            continue
        bks = [
            bk.model_copy(update={"bkcc_value": bkcc_val.upper(), "bkcc_col": bkcc_col})
            if (call.table_id is None or bk.table_id == call.table_id)
            and (call.source_column is None or bk.col_name == call.source_column)
            else bk
            for bk in h.business_keys
        ]
        hubs.append(h.model_copy(update={"business_keys": bks}))
    return draft.model_copy(update={"hubs": hubs}), [], []


def _apply_widen_decimal(
    draft: DraftVaultIR,
    call: BucketAOperatorCall,
    locks: EvolutionLockSpec | None,
) -> tuple[DraftVaultIR, list[str], list[HorizonQuestion]]:
    target_col = call.params.get("target_column") or call.source_column or ""
    prec = int(call.params.get("precision", "38"))
    scale = int(call.params.get("scale", "10"))
    dec_type = DecimalType(precision=prec, scale=scale)

    if call.target_hub and locks and call.target_hub in locks.locked_hub_bks and not call.sat_name:
        return (
            draft,
            [],
            [
                escalate_question(
                    call,
                    "BRK-02",
                    f"Locked Hub BK type widening on {call.target_hub}",
                    f"Cannot alter locked Hub BK column {target_col} in-place.",
                )
            ],
        )

    rerouted: list[str] = []
    sats: list[DraftSatelliteSpec] = []
    added_exts: list[DraftSatelliteSpec] = []

    for sat in draft.satellites:
        if call.sat_name and sat.sat_name != call.sat_name:
            sats.append(sat)
            continue
        if not call.sat_name and call.target_hub and sat.parent_name != call.target_hub:
            sats.append(sat)
            continue
        is_locked = locks is not None and sat.sat_name in locks.locked_sat_hash_diffs
        matched = next((f for f in sat.attributes if f.name == target_col), None)
        if matched is None:
            sats.append(sat)
            continue
        if is_locked and not is_lossless_type_widening(matched.logical_type, dec_type):
            existing_names = {s.sat_name for s in draft.satellites} | {
                s.sat_name for s in added_exts
            }
            epoch = next_companion_epoch(sat.sat_name, locks, [*draft.satellites, *added_exts])
            ext_name = f"{sat.sat_name}_ext_v{epoch}"
            while ext_name in existing_names:
                epoch += 1
                ext_name = f"{sat.sat_name}_ext_v{epoch}"
            rerouted.append(ext_name)
            added_exts.append(
                DraftSatelliteSpec(
                    sat_name=ext_name,
                    sat_kind="EXT",
                    parent_name=sat.parent_name,
                    source_bindings=list(sat.source_bindings[:1]),
                    extends_sat_name=sat.sat_name,
                    companion_epoch=epoch,
                    attributes=[
                        matched.model_copy(update={"logical_type": dec_type, "nullable": True})
                    ],
                )
            )
            sats.append(sat)
        else:
            attrs = [
                f.model_copy(update={"logical_type": dec_type}) if f.name == target_col else f
                for f in sat.attributes
            ]
            sats.append(sat.model_copy(update={"attributes": attrs}))
    return draft.model_copy(update={"satellites": [*sats, *added_exts]}), rerouted, []


def _apply_split_sat(
    draft: DraftVaultIR,
    call: BucketAOperatorCall,
    locks: EvolutionLockSpec | None,
) -> tuple[DraftVaultIR, list[str], list[HorizonQuestion]]:
    if not call.sat_name:
        raise ValueError("SPLIT_HIGH_FREQ_OR_PII_SAT requires sat_name")
    new_sat_name = call.params.get("new_sat_name", "").strip()
    split_reason = call.params.get("split_reason", "VELOCITY").upper()
    cols = {c.strip() for c in call.params.get("columns_csv", "").split(",") if c.strip()}
    if not new_sat_name or not cols:
        raise ValueError("SPLIT_HIGH_FREQ_OR_PII_SAT requires new_sat_name and columns_csv")

    is_locked = locks is not None and call.sat_name in locks.locked_sat_hash_diffs
    rerouted: list[str] = []
    sats: list[DraftSatelliteSpec] = []
    created: list[DraftSatelliteSpec] = []

    for sat in draft.satellites:
        if sat.sat_name != call.sat_name:
            sats.append(sat)
            continue
        moving = [
            f.model_copy(update={"is_pii": True if split_reason == "PII" else f.is_pii})
            for f in sat.attributes
            if f.name in cols
        ]
        if is_locked:
            existing_names = {s.sat_name for s in draft.satellites} | {s.sat_name for s in created}
            epoch = next_companion_epoch(sat.sat_name, locks, [*draft.satellites, *created])
            ext_name = f"{sat.sat_name}_ext_v{epoch}"
            while ext_name in existing_names:
                epoch += 1
                ext_name = f"{sat.sat_name}_ext_v{epoch}"
            rerouted.append(ext_name)
            created.append(
                DraftSatelliteSpec(
                    sat_name=ext_name,
                    sat_kind="PII" if split_reason == "PII" else "EXT",
                    parent_name=sat.parent_name,
                    source_bindings=list(sat.source_bindings[:1]),
                    extends_sat_name=sat.sat_name,
                    companion_epoch=epoch,
                    attributes=[f.model_copy(update={"nullable": True}) for f in moving],
                )
            )
            sats.append(sat)
        else:
            remaining = [f for f in sat.attributes if f.name not in cols]
            if remaining:
                sats.append(sat.model_copy(update={"attributes": remaining}))
            created.append(
                DraftSatelliteSpec(
                    sat_name=new_sat_name,
                    sat_kind="PII" if split_reason == "PII" else "STANDARD",
                    parent_name=sat.parent_name,
                    source_bindings=list(sat.source_bindings[:1]),
                    attributes=moving,
                )
            )
    return draft.model_copy(update={"satellites": [*sats, *created]}), rerouted, []


def _apply_inject_mas_cdk(
    draft: DraftVaultIR,
    call: BucketAOperatorCall,
    locks: EvolutionLockSpec | None,
) -> tuple[DraftVaultIR, list[str], list[HorizonQuestion]]:
    cdk_col = call.params.get("cdk_col") or call.source_column or ""
    if not call.sat_name or not cdk_col:
        raise ValueError("INJECT_MAS_CDK requires sat_name and params['cdk_col']")
    is_locked = locks is not None and call.sat_name in locks.locked_sat_hash_diffs
    rerouted: list[str] = []
    sats: list[DraftSatelliteSpec] = []
    added: list[DraftSatelliteSpec] = []

    for sat in draft.satellites:
        if sat.sat_name != call.sat_name:
            sats.append(sat)
            continue
        if is_locked:
            existing_names = {s.sat_name for s in draft.satellites} | {s.sat_name for s in added}
            slug = sat.sat_name.removeprefix("sat_")
            mas_name = f"mas_{slug}"
            if mas_name in existing_names:
                ver = max(2, next_companion_epoch(sat.sat_name, locks, [*draft.satellites, *added]))
                mas_name = f"mas_{slug}_v{ver}"
                while mas_name in existing_names:
                    ver += 1
                    mas_name = f"mas_{slug}_v{ver}"
            rerouted.append(mas_name)
            cdk_field = next(
                (
                    f.model_copy(update={"nullable": False})
                    for f in sat.attributes
                    if f.name == cdk_col
                ),
                DraftField(name=cdk_col, logical_type="INT64", nullable=False, is_pii=False),
            )
            added.append(
                DraftSatelliteSpec(
                    sat_name=mas_name,
                    sat_kind="MAS",
                    parent_name=sat.parent_name,
                    source_bindings=list(sat.source_bindings[:1]),
                    extends_sat_name=sat.sat_name,
                    child_dependent_keys=[cdk_col],
                    attributes=[cdk_field],
                )
            )
            sats.append(sat)
        else:
            cdks = list(dict.fromkeys([*sat.child_dependent_keys, cdk_col]))
            attrs = list(sat.attributes)
            if not any(f.name == cdk_col for f in attrs):
                attrs.append(
                    DraftField(name=cdk_col, logical_type="INT64", nullable=False, is_pii=False)
                )
            else:
                attrs = [
                    f.model_copy(update={"nullable": False}) if f.name == cdk_col else f
                    for f in attrs
                ]
            sats.append(
                sat.model_copy(
                    update={"sat_kind": "MAS", "child_dependent_keys": cdks, "attributes": attrs}
                )
            )
    return draft.model_copy(update={"satellites": [*sats, *added]}), rerouted, []


def apply_bucket_a_operators(
    draft: DraftVaultIR,
    operators: Sequence[BucketAOperatorCall],
    locks: EvolutionLockSpec | None = None,
) -> BucketAExecutionResult:
    """Apply BucketAOperatorCall mutations onto DraftVaultIR with evolution_locks rerouting."""
    current = draft
    applied: list[str] = []
    rerouted: list[str] = []
    escalations: list[HorizonQuestion] = []
    dedup_cols_by_sat: dict[str, list[str]] = {}

    dispatch = {
        "SET_BKCC": _apply_set_bkcc,
        "WIDEN_TYPE_TO_DECIMAL": _apply_widen_decimal,
        "SPLIT_HIGH_FREQ_OR_PII_SAT": _apply_split_sat,
        "PROMOTE_COLUMN_TO_HUB": apply_promote_col_to_hub,
        "PROMOTE_HUB_BK_TO_IMMUTABLE": apply_promote_hub_bk_immutable,
        "INJECT_MAS_CDK": _apply_inject_mas_cdk,
    }

    for call in operators:
        handler = dispatch[call.operator]
        current, op_rerouted, op_esc = handler(current, call, locks)
        if op_esc:
            escalations.extend(op_esc)
        else:
            applied.append(call.operator)
            if call.operator == "INJECT_MAS_CDK":
                dedup_col = call.params.get("dedup_order_col", "").strip()
                if dedup_col:
                    tgt_sat = op_rerouted[0] if op_rerouted else (call.sat_name or "")
                    if tgt_sat:
                        existing_d = dedup_cols_by_sat.setdefault(tgt_sat, [])
                        if dedup_col not in existing_d:
                            existing_d.append(dedup_col)
        rerouted.extend(op_rerouted)

    return BucketAExecutionResult(
        draft_ir=current,
        applied_ops=applied,
        rerouted_entities=list(dict.fromkeys(rerouted)),
        bucket_b_escalations=escalations,
        requires_bucket_b=bool(escalations),
        dedup_order_cols_by_sat=dedup_cols_by_sat,
    )


__all__ = ["apply_bucket_a_operators"]
