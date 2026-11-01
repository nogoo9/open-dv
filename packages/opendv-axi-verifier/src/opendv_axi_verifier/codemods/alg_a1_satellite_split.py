"""Algorithm A.1: 3-Axis Satellite Split & Gate 4 YAGNI Cost Guard.

Enforces Data Vault 2.0 Satellite splitting along three orthogonal axes:
  1. Source System (`s_src`): 1 Raw Satellite per `(parent_entity, source_bronze_table)`
  2. Security / Privacy (`c_sec`): Isolates `PII_HIGH` / `PCI` columns into `sat_<base>_pii`
  3. Change Frequency (`f_chg`): Splits general columns into `_static`, `_core`, `_high_freq`
     ONLY when the 3-part YAGNI cost guard passes:
       - `len(C_general) >= 10`
       - `Delta log10(lambda) >= 2.0`
       - `W_amp >= 50.0 MB/day`

Also merges premature micro-satellites (such as `sat_lp_commitment_fx` at
`W_amp = 4.20 MB/day < 50.0 MB/day`) back into their sibling core satellite on the
same `(parent_entity, source_bronze_table)` while strictly preserving `PII_HIGH` / `PCI`
security boundaries (such as `sat_limited_partner_pii`).
"""

from __future__ import annotations

import copy
import math
from typing import Any

PII_ISOLATION_CLASSES: frozenset[str] = frozenset({"PII_HIGH", "PCI"})
SECONDARY_CADENCE_SUFFIXES: tuple[str, ...] = (
    "_fx_high_freq",
    "_high_freq",
    "_fx",
    "_static",
    "_fast",
    "_slow",
)


def escalate_classification(classifications: list[str] | set[str] | tuple[str, ...]) -> str:
    """Return the highest security classification across a set of column classifications."""
    normalized = {str(c).upper() for c in classifications if c}
    if "PII_HIGH" in normalized:
        return "PII_HIGH"
    if "PCI" in normalized:
        return "PCI"
    if "CONFIDENTIAL" in normalized or "CONFIDENTIAL_FINANCIAL" in normalized:
        return "CONFIDENTIAL"
    if "INTERNAL" in normalized:
        return "INTERNAL"
    return "PUBLIC"


def _normalize_security_class(raw_class: str | None) -> str:
    if not raw_class:
        return "PUBLIC"
    upper = str(raw_class).upper()
    if upper in {"PII_HIGH", "PCI", "PII_SENSITIVE"}:
        return "PII_HIGH" if upper != "PCI" else "PCI"
    if upper in {"CONFIDENTIAL", "CONFIDENTIAL_FINANCIAL", "PII_LOW"}:
        return "CONFIDENTIAL"
    if upper == "INTERNAL":
        return "INTERNAL"
    return "PUBLIC"


def _set_ledger_target(entry: dict[str, Any], target_name: str) -> None:
    if "target_dv_entity" in entry:
        entry["target_dv_entity"] = target_name
    if "target_entity" in entry or "target_dv_entity" not in entry:
        entry["target_entity"] = target_name


def _ledger_matches_payload(
    entry: dict[str, Any], b_table: str, cols: list[str] | set[str]
) -> bool:
    tbl = str(entry.get("bronze_table") or entry.get("source_table") or "")
    role = str(entry.get("elm_classification") or entry.get("dv_target_role") or "")
    return (
        tbl == b_table
        and entry.get("column_name") in cols
        and role in {"SATELLITE_PAYLOAD", "DESCRIPTIVE_PAYLOAD"}
    )


def _extract_profile_lookup(
    bronze_profile: dict[str, Any],
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Normalize both flat dict profiles and structured Graph B profiles.

    Returns:
      - `col_profiles`: `{bronze_table: {column_name: {security_classification, change_freq, ...}}}`
      - `table_meta`: `{bronze_table: {row_count, daily_delta_rows, yagni_telemetry, ...}}`
    """
    col_profiles: dict[str, dict[str, dict[str, Any]]] = {}
    table_meta: dict[str, dict[str, Any]] = {}

    if not isinstance(bronze_profile, dict):
        return col_profiles, table_meta

    if "source_systems" in bronze_profile and isinstance(bronze_profile["source_systems"], list):
        for sys_entry in bronze_profile["source_systems"]:
            if not isinstance(sys_entry, dict):
                continue
            for tbl in sys_entry.get("tables", []):
                if not isinstance(tbl, dict):
                    continue
                b_table = str(tbl.get("bronze_table", ""))
                if not b_table:
                    continue
                row_count = int(tbl.get("row_count", 1_000_000))
                table_meta[b_table] = {
                    "row_count": row_count,
                    "daily_delta_rows": int(tbl.get("daily_delta_rows", 0)),
                    "yagni_telemetry": dict(tbl.get("yagni_telemetry") or {}),
                }
                t_cols: dict[str, dict[str, Any]] = {}
                for col in tbl.get("columns", []):
                    if not isinstance(col, dict) or "column_name" not in col:
                        continue
                    c_name = str(col["column_name"])
                    sec = _normalize_security_class(
                        col.get("security_classification") or col.get("pii_classification")
                    )
                    freq = col.get("change_freq")
                    if freq is None:
                        freq = col.get("daily_change_frequency")
                    if freq is None:
                        freq = col.get("update_frequency_per_row_per_day")
                    if freq is None:
                        vol_class = str(col.get("temporal_volatility_class", "")).upper()
                        if vol_class == "IMMUTABLE":
                            freq = 0.0
                        elif vol_class in {"LOW_FREQUENCY", "STATIC"}:
                            freq = 0.0005
                        elif vol_class in {"HIGH_FREQUENCY_FX", "HIGH_FREQUENCY"}:
                            freq = 0.25
                        else:
                            freq = 0.002
                    t_cols[c_name] = {
                        "security_classification": sec,
                        "change_freq": float(freq),
                        "avg_width_bytes": int(col.get("avg_width_bytes", 32)),
                        "active_rows": int(col.get("active_rows", row_count)),
                    }
                col_profiles[b_table] = t_cols
        return col_profiles, table_meta

    # Flat dictionary format `{bronze_table: {col_name: {metric: val}}}`
    for b_table, raw_tbl in bronze_profile.items():
        if not isinstance(raw_tbl, dict):
            continue
        t_cols = {}
        yagni = raw_tbl.get("yagni_telemetry")
        if isinstance(yagni, dict):
            table_meta[b_table] = {"yagni_telemetry": dict(yagni)}
        for col_name, metrics in raw_tbl.items():
            if col_name == "yagni_telemetry" or not isinstance(metrics, dict):
                continue
            sec = _normalize_security_class(
                metrics.get("security_classification") or metrics.get("pii_classification")
            )
            freq = metrics.get("change_freq")
            if freq is None:
                freq = metrics.get("daily_change_frequency", 0.0)
            t_cols[col_name] = {
                "security_classification": sec,
                "change_freq": float(freq),
                "avg_width_bytes": int(metrics.get("avg_width_bytes", 32)),
                "active_rows": int(metrics.get("active_rows", 1_000_000)),
            }
        col_profiles[b_table] = t_cols

    return col_profiles, table_meta


def _compute_w_amp_mb_per_day(
    columns: list[str],
    t_profile: dict[str, dict[str, Any]],
    t_meta: dict[str, Any],
) -> tuple[float, float]:
    """Compute `(w_amp_mb_per_day, delta_log10_lambda)` for a set of general columns."""
    yagni = t_meta.get("yagni_telemetry") or {}
    if not columns:
        return 0.0, 0.0

    lambdas = [float(t_profile.get(c, {}).get("change_freq", 0.0)) for c in columns]
    max_lambda = max(lambdas) if lambdas else 0.0
    min_lambda = min(lambdas) if lambdas else 0.0
    delta_log_lambda = math.log10(max_lambda + 1e-6) - math.log10(min_lambda + 1e-6)

    if "write_amplification_mb_per_day" in yagni:
        return float(yagni["write_amplification_mb_per_day"]), float(
            yagni.get("delta_log10_lambda", delta_log_lambda)
        )

    b_static_bytes = sum(
        int(t_profile.get(c, {}).get("avg_width_bytes", 32))
        for c in columns
        if float(t_profile.get(c, {}).get("change_freq", 0.0)) <= 1e-3
    )
    n_rows = max(
        [int(t_profile.get(c, {}).get("active_rows", 1_000_000)) for c in columns]
        or [int(t_meta.get("row_count", 1_000_000))]
    )
    w_amp_mb_per_day = (n_rows * max_lambda * b_static_bytes) / 1e6
    return w_amp_mb_per_day, delta_log_lambda


def _is_pii_satellite(sat: dict[str, Any], t_profile: dict[str, dict[str, Any]]) -> bool:
    sat_name = str(sat.get("name", ""))
    sat_sec = _normalize_security_class(sat.get("security_classification"))
    if sat_name.endswith("_pii") or sat_sec in PII_ISOLATION_CLASSES:
        return True
    payload = list(sat.get("payload_columns", []))
    if not payload:
        return False
    return all(
        _normalize_security_class(t_profile.get(c, {}).get("security_classification"))
        in PII_ISOLATION_CLASSES
        for c in payload
    )


def _select_primary_satellite(group: list[dict[str, Any]]) -> dict[str, Any]:
    """Select the primary core satellite when merging over-split micro-satellites."""
    for sat in group:
        name = str(sat.get("name", ""))
        if not name.endswith(SECONDARY_CADENCE_SUFFIXES):
            return sat
    return group[0]


def _merge_yagni_micro_satellites(
    satellites: list[dict[str, Any]],
    col_profiles: dict[str, dict[str, dict[str, Any]]],
    table_meta: dict[str, dict[str, Any]],
    ledger: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge non-PII micro-satellites on the same `(parent_entity, source_bronze_table)`
    when Gate 4 YAGNI cost threshold (`W_amp >= 50.0 MB/day` and `len >= 10`) is not met.
    """
    grouped: dict[tuple[str, str, str], list[int]] = {}
    for idx, sat in enumerate(satellites):
        if sat.get("satellite_type") != "STANDARD_SATELLITE":
            continue
        if sat.get("volatility_tier") == "ADDITIVE_DRIFT_COMPANION":
            continue
        parent = str(sat.get("parent_entity", ""))
        b_table = str(sat.get("source_bronze_table", ""))
        t_profile = col_profiles.get(b_table, {})
        if _is_pii_satellite(sat, t_profile):
            continue
        sec_class = _normalize_security_class(sat.get("security_classification"))
        grouped.setdefault((parent, b_table, sec_class), []).append(idx)

    merged_indices_to_skip: set[int] = set()
    rename_map: dict[str, str] = {}
    merge_events: list[dict[str, Any]] = []

    for (_parent, b_table, _sec_class), indices in grouped.items():
        if len(indices) <= 1:
            continue
        t_profile = col_profiles.get(b_table, {})
        t_meta = table_meta.get(b_table, {})
        combined_cols: list[str] = []
        for i in indices:
            for col in satellites[i].get("payload_columns", []):
                if col not in combined_cols:
                    combined_cols.append(col)

        w_amp, delta_log = _compute_w_amp_mb_per_day(combined_cols, t_profile, t_meta)
        yagni = t_meta.get("yagni_telemetry") or {}
        exceeds_threshold = bool(
            yagni.get(
                "exceeds_50mb_split_threshold",
                len(combined_cols) >= 10 and delta_log >= 2.0 and w_amp >= 50.0,
            )
        )
        if not exceeds_threshold:
            group_sats = [satellites[i] for i in indices]
            primary = _select_primary_satellite(group_sats)
            primary_name = str(primary["name"])
            merged_cols_count = 0
            merged_from: list[str] = []
            for i in indices:
                sat = satellites[i]
                if sat is primary:
                    continue
                merged_indices_to_skip.add(i)
                sec_name = str(sat["name"])
                merged_from.append(sec_name)
                rename_map[sec_name] = primary_name
                merged_cols_count += len(sat.get("payload_columns", []))

            primary["payload_columns"] = sorted(set(combined_cols))
            all_secs = [
                _normalize_security_class(
                    t_profile.get(c, {}).get(
                        "security_classification", primary.get("security_classification")
                    )
                )
                for c in combined_cols
            ]
            if all_secs:
                primary["security_classification"] = escalate_classification(all_secs)
            merge_events.append(
                {
                    "primary_satellite": primary_name,
                    "merged_satellites": merged_from,
                    "merged_column_count": merged_cols_count,
                    "w_amp_mb_per_day": round(w_amp, 2),
                }
            )

    if rename_map:
        for entry in ledger:
            target = str(entry.get("target_entity") or entry.get("target_dv_entity") or "")
            if target in rename_map:
                if "target_dv_entity" in entry:
                    entry["target_dv_entity"] = rename_map[target]
                if "target_entity" in entry or "target_dv_entity" not in entry:
                    entry["target_entity"] = rename_map[target]

    filtered = [sat for idx, sat in enumerate(satellites) if idx not in merged_indices_to_skip]
    return filtered, merge_events


def codemod_split_satellites_3axis(
    contract: dict[str, Any],
    bronze_profile: dict[str, Any],
) -> dict[str, Any]:
    """Algorithm A.1: 3-Axis Satellite Split & Gate 4 YAGNI Cost Guard.

    1. Consolidates premature non-PII micro-satellites on the same `(parent_entity,
       source_bronze_table)` when `W_amp < 50.0 MB/day` (e.g. `sat_lp_commitment_fx`
       at `4.20 MB/day < 50.0 MB/day` into `sat_lp_commitment_investran`).
    2. Isolates `PII_HIGH` and `PCI` columns into `sat_<base>_pii` while preserving
       existing dedicated PII satellites (`sat_limited_partner_pii`).
    3. Splits remaining general columns into `_static`, `_core`, and `_high_freq`
       only when `len(C_general) >= 10`, `Delta log10(lambda) >= 2.0`, and
       `W_amp >= 50.0 MB/day`.
    """
    ir = copy.deepcopy(contract)
    col_profiles, table_meta = _extract_profile_lookup(bronze_profile)
    ledger: list[dict[str, Any]] = ir.get("column_triage_ledger", [])

    initial_sats = list(ir.get("satellites", []))
    consolidated_sats, _merge_events = _merge_yagni_micro_satellites(
        initial_sats, col_profiles, table_meta, ledger
    )

    new_satellites: list[dict[str, Any]] = []

    for sat in consolidated_sats:
        if sat.get("satellite_type") != "STANDARD_SATELLITE":
            new_satellites.append(sat)
            continue

        b_table = str(sat.get("source_bronze_table", ""))
        t_profile = col_profiles.get(b_table, {})
        t_meta = table_meta.get(b_table, {})
        base_slug = str(sat.get("name", "")).removeprefix("sat_")
        payload = list(sat.get("payload_columns", []))

        # If this satellite is already a dedicated PII satellite (or all its columns are PII),
        # preserve it intact with sorted payload columns.
        if sat.get("name", "").endswith("_pii") or (
            sat.get("security_classification") in PII_ISOLATION_CLASSES
            and all(
                _normalize_security_class(
                    t_profile.get(c, {}).get(
                        "security_classification", sat.get("security_classification")
                    )
                )
                in PII_ISOLATION_CLASSES
                for c in payload
            )
        ):
            sat_copy = copy.deepcopy(sat)
            sat_copy["payload_columns"] = sorted(payload)
            new_satellites.append(sat_copy)
            continue

        # 1. Partition by Security Classification (PII_HIGH / PCI vs General)
        c_pii = [
            c
            for c in payload
            if _normalize_security_class(t_profile.get(c, {}).get("security_classification"))
            in PII_ISOLATION_CLASSES
        ]
        c_general = [c for c in payload if c not in c_pii]

        if c_pii:
            sat_pii = copy.deepcopy(sat)
            pii_name = sat["name"] if str(sat["name"]).endswith("_pii") else f"sat_{base_slug}_pii"
            sat_pii["name"] = pii_name
            sat_pii["security_classification"] = escalate_classification(
                [
                    _normalize_security_class(
                        t_profile.get(c, {}).get("security_classification", "PII_HIGH")
                    )
                    for c in c_pii
                ]
            )
            sat_pii["volatility_tier"] = sat.get("volatility_tier") or "MIXED_PII"
            sat_pii["payload_columns"] = sorted(c_pii)
            new_satellites.append(sat_pii)
            for entry in ledger:
                if _ledger_matches_payload(entry, b_table, c_pii):
                    _set_ledger_target(entry, pii_name)

        if not c_general:
            continue

        # 2. Evaluate Gate 4 YAGNI Cost Model on c_general
        w_amp_mb_per_day, delta_log_lambda = _compute_w_amp_mb_per_day(c_general, t_profile, t_meta)

        if len(c_general) >= 10 and delta_log_lambda >= 2.0 and w_amp_mb_per_day >= 50.0:
            buckets: dict[str, list[str]] = {
                "static": [
                    c
                    for c in c_general
                    if float(t_profile.get(c, {}).get("change_freq", 0.0)) <= 1e-3
                ],
                "core": [
                    c
                    for c in c_general
                    if 1e-3 < float(t_profile.get(c, {}).get("change_freq", 0.0)) <= 5e-2
                ],
                "high_freq": [
                    c
                    for c in c_general
                    if float(t_profile.get(c, {}).get("change_freq", 0.0)) > 5e-2
                ],
            }
            # Prevent 1-column micro-satellites by folding singletons into core
            for tier in ("static", "high_freq"):
                if len(buckets[tier]) == 1:
                    buckets["core"].extend(buckets[tier])
                    buckets[tier] = []
            if len(buckets["core"]) == 1:
                target_tier = (
                    "static" if len(buckets["static"]) >= len(buckets["high_freq"]) else "high_freq"
                )
                buckets[target_tier].extend(buckets["core"])
                buckets["core"] = []

            for tier_name, cols in buckets.items():
                if len(cols) >= 2:
                    sat_tier = copy.deepcopy(sat)
                    tier_sat_name = f"sat_{base_slug}_{tier_name}"
                    sat_tier["name"] = tier_sat_name
                    sat_tier["security_classification"] = escalate_classification(
                        [
                            _normalize_security_class(
                                t_profile.get(c, {}).get(
                                    "security_classification",
                                    sat.get("security_classification", "PUBLIC"),
                                )
                            )
                            for c in cols
                        ]
                    )
                    sat_tier["volatility_tier"] = tier_name.upper()
                    sat_tier["payload_columns"] = sorted(cols)
                    new_satellites.append(sat_tier)
                    for entry in ledger:
                        if _ledger_matches_payload(entry, b_table, cols):
                            _set_ledger_target(entry, tier_sat_name)
        else:
            # YAGNI Guard triggered: keep general columns consolidated in a single satellite
            sat_core = copy.deepcopy(sat)
            core_name = str(sat["name"]) if not c_pii else f"sat_{base_slug}_core"
            sat_core["name"] = core_name
            base_sec = _normalize_security_class(sat.get("security_classification", "PUBLIC"))
            col_secs = [
                _normalize_security_class(
                    t_profile.get(c, {}).get("security_classification", base_sec)
                )
                for c in c_general
            ]
            if not c_pii:
                col_secs.append(base_sec)
            sat_core["security_classification"] = escalate_classification(col_secs)
            sat_core["payload_columns"] = sorted(c_general)
            new_satellites.append(sat_core)
            for entry in ledger:
                if _ledger_matches_payload(entry, b_table, c_general):
                    _set_ledger_target(entry, core_name)

    ir["satellites"] = new_satellites
    return ir


codemod_2_split_satellite_by_cadence_and_classification = codemod_split_satellites_3axis
