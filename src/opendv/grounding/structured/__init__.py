"""Synchronous structured tabular grounding & shape-profiling package (Layer-2a)."""

from __future__ import annotations

from opendv.grounding.structured.engine import ColumnGroundingEngine, SymbolicGroundingDraft
from opendv.grounding.structured.profiler import compute_column_shape_stats
from opendv.grounding.structured.validators import (
    PATTERN_VALIDATORS,
    STRUCTURAL_PATTERN_KEYS,
    compute_structural_patterns,
    is_valid_aba_routing,
    is_valid_cusip,
    is_valid_figi,
    is_valid_iban,
    is_valid_isin,
    is_valid_iso_3166_country,
    is_valid_iso_4217_ccy,
    is_valid_iso_date,
    is_valid_iso_timestamp,
    is_valid_lei,
    is_valid_sedol,
    is_valid_swift_bic,
    is_valid_uuid_v4,
)

__all__ = [
    "PATTERN_VALIDATORS", "STRUCTURAL_PATTERN_KEYS", "ColumnGroundingEngine",
    "SymbolicGroundingDraft", "compute_column_shape_stats",
    "compute_structural_patterns", "is_valid_aba_routing", "is_valid_cusip", "is_valid_figi",
    "is_valid_iban", "is_valid_isin", "is_valid_iso_3166_country", "is_valid_iso_4217_ccy",
    "is_valid_iso_date", "is_valid_iso_timestamp", "is_valid_lei", "is_valid_sedol",
    "is_valid_swift_bic", "is_valid_uuid_v4",
]
