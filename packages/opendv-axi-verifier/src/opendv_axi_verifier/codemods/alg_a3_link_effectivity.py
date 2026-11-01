"""Algorithm A.3: Link Effectivity Satellite (`esat_*`) Auto-Injection.

Inspects CDC foreign-key reassignment/turnover rates (`swap_rate > 0.0`) on
`STANDARD_LINK` entities, binds `driving_key_hub` to the anchor Hub whose foreign
key exhibits the highest positive partner-turnover rate, and injects a dedicated
`EFFECTIVITY_SATELLITE` (`esat_<link_slug>`) when not already present.
"""

from __future__ import annotations

import copy
from typing import Any


def _normalize_cdc_profile(
    bronze_link_cdc_profile: dict[str, Any] | None,
) -> dict[str, dict[str, float]]:
    if not bronze_link_cdc_profile or not isinstance(bronze_link_cdc_profile, dict):
        return {}

    if "source_systems" in bronze_link_cdc_profile and isinstance(
        bronze_link_cdc_profile["source_systems"], list
    ):
        extracted: dict[str, dict[str, float]] = {}
        for sys_entry in bronze_link_cdc_profile["source_systems"]:
            if not isinstance(sys_entry, dict):
                continue
            for tbl in sys_entry.get("tables", []):
                if not isinstance(tbl, dict):
                    continue
                b_table = str(tbl.get("bronze_table", ""))
                if not b_table:
                    continue
                col_rates: dict[str, float] = {}
                for col in tbl.get("columns", []):
                    if isinstance(col, dict) and "column_name" in col:
                        rate = col.get("fk_swap_rate", col.get("swap_rate", 0.0))
                        if rate is not None:
                            col_rates[str(col["column_name"])] = float(rate)
                if col_rates:
                    extracted[b_table] = col_rates
        return extracted

    result: dict[str, dict[str, float]] = {}
    for b_table, cols in bronze_link_cdc_profile.items():
        if isinstance(cols, dict):
            result[str(b_table)] = {
                str(k): float(v) for k, v in cols.items() if isinstance(v, int | float)
            }
    return result


def codemod_inject_link_effectivity(
    contract: dict[str, Any],
    bronze_link_cdc_profile: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    """Algorithm A.3: Auto-inject `esat_<link_slug>` Effectivity Satellites."""
    ir = copy.deepcopy(contract)
    cdc_lookup = _normalize_cdc_profile(bronze_link_cdc_profile)

    existing_esat_parents = {
        str(s.get("parent_entity", ""))
        for s in ir.get("satellites", [])
        if s.get("satellite_type") == "EFFECTIVITY_SATELLITE"
    }

    satellites: list[dict[str, Any]] = list(ir.get("satellites", []))

    for lnk in ir.get("links", []):
        if lnk.get("link_type") != "STANDARD_LINK":
            continue

        b_table = str(lnk.get("source_bronze_table", ""))
        cdc_rates = cdc_lookup.get(b_table, {})

        best_ref: dict[str, Any] | None = None
        max_swap_rate = 0.0

        for ref in lnk.get("hub_references", []):
            fk_cols = list(ref.get("source_fk_columns", []))
            if not fk_cols:
                continue
            fk_col = str(fk_cols[0])
            swap_rate = float(cdc_rates.get(fk_col, 0.0))
            if swap_rate > max_swap_rate:
                max_swap_rate = swap_rate
                best_ref = ref

        if best_ref is not None and max_swap_rate > 0.0:
            lnk["driving_key_hub"] = best_ref["target_hub"]
            lnk_name = str(lnk["name"])
            if lnk_name not in existing_esat_parents:
                slug = lnk_name.removeprefix("lnk_")
                esat_name = f"esat_{slug}"
                satellites.append(
                    {
                        "name": esat_name,
                        "parent_entity": lnk_name,
                        "satellite_type": "EFFECTIVITY_SATELLITE",
                        "source_bronze_table": b_table,
                        "security_classification": "PUBLIC",
                        "volatility_tier": "CORE",
                        "sub_sequence_column": None,
                        "driving_key_column": best_ref["source_fk_columns"][0],
                        "payload_columns": ["START_DTS", "END_DTS", "IS_CURRENT_FLAG"],
                        "tracked_columns": None,
                        "soft_deprecated_columns": [],
                    }
                )
                existing_esat_parents.add(lnk_name)

    ir["satellites"] = satellites
    return ir


codemod_4_inject_effectivity_satellite = codemod_inject_link_effectivity
