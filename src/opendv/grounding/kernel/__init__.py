"""Modality-neutral grounding kernel primitives (Layer-2a)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from opendv.grounding.kernel.calibration import (
    CalibrationOutcome,
    CandidateChannelInputs,
    ColumnCalibrationContext,
    ScoredCandidate,
    check_fast_path_eligible,
    compute_active_channels,
    compute_logit_normal_ci,
    compute_observed_channels,
    compute_phi_agent,
    compute_phi_anchor,
    compute_phi_lex,
    compute_sigma_disagree,
    compute_sigma_eff,
    determine_suggested_predicate,
    normalize_column_tokens,
    normalize_property_label,
    select_and_calibrate,
    softmax_with_null,
)
from opendv.grounding.kernel.lexical import (
    STOPWORDS,
    LexicalIndex,
    LexicalMatcherConfig,
    LexicalWeightsConfig,
    load_lexical_matcher_config,
)
from opendv.grounding.kernel.overlay import OverlayKnowledgeSource, ProjectGraphASource
from opendv.grounding.kernel.pii_core import (
    MASKED_PII_TOKEN,
    REDACTED_LEAK_RE,
    compute_shannon_entropy,
    detect_pii_in_column_name,
    detect_pii_in_value,
    ensure_no_redacted_placeholder,
    find_high_entropy_tokens,
    has_redacted_placeholder,
    is_non_credential_digest_column,
    is_valid_aba_routing,
    is_valid_luhn,
    mask_cell_value,
    mask_pii_text,
    strip_pii_spans,
)


@dataclass(frozen=True, slots=True)
class IsoCodesTable:
    """Immutable reference table of ISO 4217 currencies and ISO 3166-1 countries."""

    currency_codes: frozenset[str]
    currency_names: Mapping[str, str]
    country_alpha2: frozenset[str]
    country_alpha3: frozenset[str]
    alpha3_to_alpha2: Mapping[str, str]
    country_names: Mapping[str, str]
    fibo_currency_uri: str
    fibo_country_uri: str


@lru_cache(maxsize=1)
def load_iso_codes() -> IsoCodesTable:
    """Load and cache ISO 4217 currency and ISO 3166-1 country codes from iso_codes.json."""
    iso_path = Path(__file__).resolve().parent / "iso_codes.json"
    raw = json.loads(iso_path.read_text(encoding="utf-8"))
    raw_ccy = raw["iso_4217_currencies"]
    raw_countries = raw["iso_3166_1_countries"]

    currency_names = {code.upper(): str(info["name"]) for code, info in raw_ccy.items()}
    alpha3_to_alpha2: dict[str, str] = {}
    country_names: dict[str, str] = {}
    for a2, info in raw_countries.items():
        a2_up = a2.upper()
        a3_up = str(info["alpha3"]).upper()
        name = str(info["name"])
        alpha3_to_alpha2[a3_up] = a2_up
        country_names[a2_up] = name
        country_names[a3_up] = name

    return IsoCodesTable(
        currency_codes=frozenset(currency_names.keys()),
        currency_names=currency_names,
        country_alpha2=frozenset(a2.upper() for a2 in raw_countries),
        country_alpha3=frozenset(alpha3_to_alpha2.keys()),
        alpha3_to_alpha2=alpha3_to_alpha2,
        country_names=country_names,
        fibo_currency_uri=str(raw["fibo_currency_uri"]),
        fibo_country_uri=str(raw["fibo_country_uri"]),
    )


__all__ = [
    "MASKED_PII_TOKEN",
    "REDACTED_LEAK_RE",
    "STOPWORDS",
    "CalibrationOutcome",
    "CandidateChannelInputs",
    "ColumnCalibrationContext",
    "IsoCodesTable",
    "LexicalIndex",
    "LexicalMatcherConfig",
    "LexicalWeightsConfig",
    "OverlayKnowledgeSource",
    "ProjectGraphASource",
    "ScoredCandidate",
    "check_fast_path_eligible",
    "compute_active_channels",
    "compute_logit_normal_ci",
    "compute_observed_channels",
    "compute_phi_agent",
    "compute_phi_anchor",
    "compute_phi_lex",
    "compute_shannon_entropy",
    "compute_sigma_disagree",
    "compute_sigma_eff",
    "detect_pii_in_column_name",
    "detect_pii_in_value",
    "determine_suggested_predicate",
    "ensure_no_redacted_placeholder",
    "find_high_entropy_tokens",
    "has_redacted_placeholder",
    "is_non_credential_digest_column",
    "is_valid_aba_routing",
    "is_valid_luhn",
    "load_iso_codes",
    "load_lexical_matcher_config",
    "mask_cell_value",
    "mask_pii_text",
    "normalize_column_tokens",
    "normalize_property_label",
    "select_and_calibrate",
    "softmax_with_null",
    "strip_pii_spans",
]
