"""CanonicalIR Schema Inspection, Entity Naming, and Seed Synthesis Utilities."""

from __future__ import annotations

import re
from typing import Any

from opendv_core.constants import GHOST_DTS, GLOBAL_BKCC
from opendv_core.hashing import compute_dv2_length_prefixed_sha256
from opendv_core.models.canonical_ir import (
    CanonicalIR,
    IRColumnDef,
    IRHub,
    IRLink,
    IRPitTable,
    IRSatellite,
)
from opendv_duckdb_sandbox.compiler_4zone.hashing import _normalize_duckdb_type


def _hub_slug(hub_name: str) -> str:
    """Derive an uppercase business key token prefix from a Hub table name."""
    return hub_name.removeprefix("hub_").removeprefix("bhub_").upper()


def _effective_hub_bkcc_column_expr(hub: IRHub) -> str | None:
    """Return the dynamic BKCC column expression configured on `hub`, if any."""
    for candidate in (
        hub.bkcc_column_expr,
        hub.hk_column.hash_expression.bkcc_column_expr,
        *(sm.bkcc_column_expr for sm in hub.source_mappings),
    ):
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def _effective_hub_bkcc_literal(hub: IRHub) -> str | None:
    """Return the static BKCC literal configured on `hub`, if any."""
    for candidate in (
        hub.bkcc_literal,
        hub.hk_column.hash_expression.bkcc_literal,
        *(sm.bkcc_literal for sm in hub.source_mappings),
    ):
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def _effective_hub_sm_bkcc_literal(hub: IRHub, sm: Any) -> str | None:
    """Return the static BKCC literal configured on a specific source mapping of `hub`."""
    for candidate in (
        getattr(sm, "bkcc_literal", None),
        getattr(sm, "bkcc_prefix", None),
        _effective_hub_bkcc_literal(hub),
    ):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return None


def _hub_seed_bk_values(
    hub: IRHub,
    row_idx: int,
    *,
    bkcc_override: str | None = None,
) -> list[str]:
    """Return deterministic business key token values for `row_idx` of `hub`."""
    slug = _hub_slug(hub.name)
    bkcc_col_expr = _effective_hub_bkcc_column_expr(hub)
    bkcc_lit = _effective_hub_bkcc_literal(hub)
    values: list[str] = []
    for bk in hub.business_keys:
        if bk.is_bkcc:
            if bkcc_override:
                values.append(bkcc_override)
            elif bkcc_col_expr or bk.is_tenant_id:
                values.append(f"TENANT_{row_idx:03d}")
            elif bkcc_lit:
                values.append(bkcc_lit)
            else:
                values.append("GLOBAL")
        else:
            col_name = bk.name.lower()
            if col_name in {"lei_code", "lei", "portco_natural_key"}:
                values.append("529900T8BM49AURSDO55" if row_idx == 1 else f"{slug}-{row_idx:03d}")
            elif col_name in {"lp_id", "lp_num", "lp_natural_key", "investor_id"}:
                values.append(f"{'LP'}-{100 + row_idx}")
            elif col_name in {"fund_code", "fund_id"}:
                values.append("APEX-BUYOUT-IV" if row_idx == 1 else f"{slug}-{row_idx:03d}")
            else:
                values.append(f"{slug}-{row_idx:03d}")
    return values


def _hub_seed_hk(
    hub: IRHub,
    row_idx: int,
    *,
    bkcc_override: str | None = None,
) -> str:
    """Compute the deterministic lowercase 64-char SHA-256 hash key for `row_idx` of `hub`."""
    tokens = _hub_seed_bk_values(hub, row_idx, bkcc_override=bkcc_override)
    has_bkcc_col = any(bool(bk.is_bkcc) for bk in hub.business_keys)
    return compute_dv2_length_prefixed_sha256(
        tokens,
        is_business_key=True,
        bkcc_prefix=None
        if has_bkcc_col
        else (bkcc_override or _effective_hub_bkcc_literal(hub) or GLOBAL_BKCC),
    ).lower()


def _satellite_seed_sql_literal(col: IRColumnDef, row_idx: int) -> str:
    """Return a deterministic SQL literal for a Satellite payload column at `row_idx`."""
    col_lower = col.name.lower()
    norm_type = _normalize_duckdb_type(col.data_type)

    # Governance & provenance columns (DV-DYN-011, DV-DYN-012, DV-DYN-013)
    if col_lower == "is_active_flag":
        return "TRUE"
    if col_lower in {"effective_to_dts", "effective_to_date"}:
        return "NULL"
    if col_lower == "effective_from_dts":
        return "TIMESTAMP '2026-10-01 06:00:00'"
    if col_lower in {
        "effective_from_date",
        "effective_from_dt",
        "as_of_quarter_end",
        "reporting_quarter",
    }:
        return "DATE '2026-09-30'"
    if col_lower == "extractor_model_id":
        return "'gemini-2.5-pro'"
    if col_lower.endswith("_sha256") or norm_type == "CHAR(64)":
        return "'9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08'"
    if col_lower in {
        "extraction_confidence",
        "confidence_score",
        "tier2_token_jaccard",
        "tier3_cosine_sim",
    }:
        return "0.9500" if row_idx == 1 else ("0.9200" if row_idx == 2 else "0.9800")
    if col_lower in {"human_review_status", "routing_action", "steward_gate_status"}:
        if col_lower == "steward_gate_status":
            return "'STEWARD_HOLD'" if row_idx == 2 else "'APPROVED'"
        return "'AUTO_ACCEPTED_TIER_2'"
    if col_lower.endswith("_quote") or col_lower in {"source_text_quote", "verbatim_quote"}:
        return f"'Verified contractual clause quote #{row_idx} from source document.'"
    if col_lower in {"source_page_number", "page_number"}:
        return str(row_idx)
    if col_lower in {"source_char_start", "char_start"}:
        return str(10 * row_idx)
    if col_lower in {"source_char_end", "char_end"}:
        return str(10 * row_idx + 45)

    # Financial / Valuation benchmark columns
    if col_lower in {"entry_ev_multiple", "ev_ebitda_multiple"}:
        return "12.80" if row_idx == 1 else "14.50"
    if col_lower == "entry_ebitda_usd":
        return "16896550.00"
    if col_lower in {"current_nav_usd", "enterprise_value_usd"}:
        return "216280000.00" if row_idx == 1 else "245000000.00"
    if col_lower in {"committed_amount", "committed_usd", "commitment_amount_usd"}:
        return "150000000.00" if row_idx == 1 else "95000000.00"
    if col_lower in {"reconciled_legacy_nav_usd", "ending_nav_amt"}:
        return "112500000.00"
    if col_lower in {"side_letter_fee_discount_bps", "mgmt_fee_discount_bps"}:
        return "25"
    if col_lower in {"has_mfn_protection", "mfn_election_flag"}:
        return "TRUE"

    if norm_type == "BOOLEAN":
        return "TRUE" if row_idx % 2 == 1 else "FALSE"
    if norm_type in {"INTEGER", "BIGINT"}:
        return str(100 * row_idx)
    if norm_type.startswith(("DECIMAL", "NUMERIC", "DOUBLE")):
        return f"{1000 * row_idx + 0.50:.2f}"
    if norm_type == "DATE":
        return "DATE '2026-09-30'"
    if norm_type == "TIMESTAMP":
        return "TIMESTAMP '2026-10-01 06:00:00'"
    if col_lower in {"company_name", "company_legal_name"}:
        return "'Nordic Cloud Infrastructure AB'" if row_idx == 1 else f"'{col.name}_val_{row_idx}'"
    if col_lower in {"lei_code", "lei", "portco_natural_key"}:
        return "'529900T8BM49AURSDO55'" if row_idx == 1 else f"'{col.name}_val_{row_idx}'"
    return f"'{col.name}_val_{row_idx}'"


def _qualify_steward_predicate(predicate: str | None) -> str:
    """Qualify a link's `steward_filter_predicate` for a Zone 2 `FROM ... src` query."""
    raw = (predicate or "steward_gate_status = 'APPROVED'").strip()
    if not raw:
        return "src.steward_gate_status = 'APPROVED'"
    return re.sub(r"(?<!\.)\bsteward_gate_status\b", "src.steward_gate_status", raw)


def _link_schema(link: IRLink) -> str:
    """Return the target Vault schema (`business_vault` or `raw_vault`) for `link`."""
    if link.name.startswith(("sal_", "hal_")) or link.link_type == "SAME_AS":
        return "business_vault"
    return "raw_vault"


def _satellite_schema(sat: IRSatellite) -> str:
    """Return the target Vault schema (`business_vault` or `raw_vault`) for `sat`."""
    if sat.satellite_type in {"BUSINESS", "EFFECTIVITY"} or sat.name.startswith("esat_"):
        return "business_vault"
    return "raw_vault"


def _satellite_child_key_names(sat: IRSatellite) -> list[str]:
    """Return the sub-sequence column names (`child_dependent_keys`) for `sat`."""
    return [c.name if hasattr(c, "name") else str(c) for c in (sat.child_dependent_keys or [])]


def _satellite_all_data_columns(sat: IRSatellite) -> list[IRColumnDef]:
    """Return sub-sequence and payload columns without duplicates or duplicate `applied_dts`."""
    child_defs = list(sat.child_dependent_keys or [])
    sub_seq_set = {c.name if hasattr(c, "name") else str(c) for c in child_defs}
    applied_col_name = sat.applied_dts_column.name if sat.applied_dts_column is not None else None
    existing_names = {c.name for c in sat.payload_columns}
    cols: list[IRColumnDef] = []
    for c_def in child_defs:
        seq_name = c_def.name if hasattr(c_def, "name") else str(c_def)
        if seq_name not in existing_names and seq_name != applied_col_name:
            if isinstance(c_def, IRColumnDef):
                cols.append(c_def.model_copy(update={"nullable": False}))
            else:
                cols.append(
                    IRColumnDef(
                        name=seq_name,
                        data_type="INTEGER" if seq_name.endswith("_seq") else "VARCHAR",
                        nullable=False,
                    )
                )
    for col in sat.payload_columns:
        if col.name == applied_col_name:
            continue
        if col.name in sub_seq_set and col.nullable:
            cols.append(col.model_copy(update={"nullable": False}))
        else:
            cols.append(col)
    return cols


def _ghost_literal_for_sat_column(col: IRColumnDef, sub_seq_set: set[str]) -> str:
    """Return the Ghost Record SQL literal for a Satellite column (`NOT NULL` for PK sub-seq)."""
    if col.name not in sub_seq_set:
        return "NULL"
    norm = _normalize_duckdb_type(col.data_type)
    if norm in {"INTEGER", "BIGINT"} or norm.startswith(("DECIMAL", "NUMERIC", "DOUBLE")):
        return "0"
    if norm == "BOOLEAN":
        return "FALSE"
    if norm == "DATE":
        return "DATE '1900-01-01'"
    if norm == "TIMESTAMP":
        return f"TIMESTAMP '{GHOST_DTS}'"
    return "'_GHOST_'"


def _effective_pit_tables(ir_obj: CanonicalIR) -> list[IRPitTable]:
    """Return configured `IRPitTable` list or synthesize a default PIT table if omitted."""
    hub_names = {h.name for h in ir_obj.hubs}
    sat_by_name = {s.name: s for s in ir_obj.satellites}
    if ir_obj.pit_tables:
        valid_pits: list[IRPitTable] = []
        for pit in ir_obj.pit_tables:
            if pit.parent_entity in hub_names:
                tracked = [
                    s
                    for s in pit.tracked_satellites
                    if s in sat_by_name
                    and sat_by_name[s].parent_entity == pit.parent_entity
                    and sat_by_name[s].satellite_type != "MULTI_ACTIVE"
                ]
                if not tracked:
                    tracked = [
                        s
                        for s in pit.tracked_satellites
                        if s in sat_by_name and sat_by_name[s].parent_entity == pit.parent_entity
                    ]
                if tracked:
                    valid_pits.append(
                        IRPitTable(
                            name=pit.name,
                            parent_entity=pit.parent_entity,
                            as_of_dts_column=pit.as_of_dts_column,
                            snapshot_window_policy=pit.snapshot_window_policy or "LOGARITHMIC",
                            tracked_satellites=tracked,
                        )
                    )
        if valid_pits:
            return valid_pits

    for hub in ir_obj.hubs:
        attached = [
            s.name
            for s in ir_obj.satellites
            if s.parent_entity == hub.name and s.satellite_type != "MULTI_ACTIVE"
        ]
        if not attached:
            attached = [s.name for s in ir_obj.satellites if s.parent_entity == hub.name]
        if attached:
            slug = hub.name.removeprefix("hub_").removeprefix("bhub_")
            return [
                IRPitTable(
                    name=f"pit_{slug}",
                    parent_entity=hub.name,
                    as_of_dts_column="as_of_dts",
                    snapshot_window_policy="LOGARITHMIC",
                    tracked_satellites=attached,
                )
            ]
    return []


def _find_bitemporal_satellite(ir_obj: CanonicalIR) -> IRSatellite | None:
    """Identify the primary satellite subject to bi-temporal restatement or PIT tracking."""
    if not ir_obj.satellites:
        return None
    effective_pits = _effective_pit_tables(ir_obj)
    pit_tracked = {sat_name for pit in effective_pits for sat_name in pit.tracked_satellites}
    valuation_tracked = next(
        (
            s
            for s in ir_obj.satellites
            if s.name in pit_tracked
            and any(
                c.name.lower() in {"entry_ev_multiple", "ev_ebitda_multiple", "current_nav_usd"}
                for c in s.payload_columns
            )
        ),
        None,
    )
    if valuation_tracked is not None:
        return valuation_tracked
    applied_sat = next((s for s in ir_obj.satellites if s.applied_dts_column is not None), None)
    if applied_sat is not None:
        return applied_sat
    valuation_any = next(
        (
            s
            for s in ir_obj.satellites
            if any(
                c.name.lower() in {"entry_ev_multiple", "ev_ebitda_multiple", "current_nav_usd"}
                for c in s.payload_columns
            )
        ),
        None,
    )
    if valuation_any is not None:
        return valuation_any
    pit_any = next((s for s in ir_obj.satellites if s.name in pit_tracked), None)
    if pit_any is not None:
        return pit_any
    return ir_obj.satellites[0]


__all__ = [
    "_effective_hub_bkcc_column_expr",
    "_effective_hub_bkcc_literal",
    "_effective_hub_sm_bkcc_literal",
    "_effective_pit_tables",
    "_find_bitemporal_satellite",
    "_ghost_literal_for_sat_column",
    "_hub_seed_bk_values",
    "_hub_seed_hk",
    "_hub_slug",
    "_link_schema",
    "_qualify_steward_predicate",
    "_satellite_all_data_columns",
    "_satellite_child_key_names",
    "_satellite_schema",
    "_satellite_seed_sql_literal",
]
