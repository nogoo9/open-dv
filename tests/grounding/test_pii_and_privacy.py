"""M2.1 tests for pii_core, raw-PII stripping, RerankColumnPacket privacy & masked calibration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from opendv.grounding.kernel import pii_core
from opendv.grounding.kernel.calibration import (
    CandidateChannelInputs,
    ColumnCalibrationContext,
    select_and_calibrate,
)
from opendv.grounding.kernel.pii_core import (
    MASKED_PII_TOKEN,
    detect_pii_in_value,
    ensure_no_redacted_placeholder,
    has_redacted_placeholder,
    strip_pii_spans,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.grounding import (
    GroundingPolicy,
    InferredValueProfile,
    SemanticContext,
)
from opendv.models.grounding_spi import (
    CalibrationParams,
    RerankCandidateSummary,
    RerankColumnPacket,
)
from opendv.models.sources import ColumnShapeStats
from opendv.sources import pii as sources_pii


def test_sources_pii_reexport_identity() -> None:
    """Backwards-compatible opendv.sources.pii re-exports match pii_core symbols."""
    for name in sources_pii.__all__:
        assert getattr(sources_pii, name) is getattr(pii_core, name)


def test_strip_pii_spans_zero_redacted_placeholders() -> None:
    """strip_pii_spans removes raw PII and placeholders without emitting [REDACTED_PII]."""
    samples = [
        "SSN 123-45-6789 belongs to alice@example.com",
        "Call 212-555-0199 or use EIN 12-3456789",
        "Routing ABA 021000021 IBAN DE89370400440532013000",
        "Card 4111-1111-1111-1111 secret sK9$mP2xQ7rT4vW8zY1bN5cM3!",
        "Hex a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0",
        f"Pre-masked {MASKED_PII_TOKEN} and [REDACTED_PII] or [REDACTED_EMAIL]",
    ]
    for raw in samples:
        cleaned = strip_pii_spans(raw)
        assert "[REDACTED_PII]" not in cleaned
        assert MASKED_PII_TOKEN not in cleaned
        assert not has_redacted_placeholder(cleaned)
        ensure_no_redacted_placeholder(cleaned, context="Unit test")
        assert detect_pii_in_value(cleaned) == []

    # Digest columns remain exempt from HEX_64 / high-entropy stripping
    digest = "a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0"
    assert strip_pii_spans(digest, col_name="hk_legal_entity") == digest

    # BUG-2: Nested and case-insensitive placeholders converge to clean text without leaks
    nested_samples = [
        "<MASKED_<MASKED_PII>PII>",
        "[REDACTED<MASKED_PII>_PII]",
        "[REDACTED[REDACTED_PII]_PII]",
        "[REDACTED_alice@example.com]",
        "[redacted_pii]",
        "<masked_pii>",
    ]
    for nested in nested_samples:
        cleaned = strip_pii_spans(nested)
        assert cleaned == ""
        assert not has_redacted_placeholder(cleaned)
        ensure_no_redacted_placeholder(cleaned, context="Nested placeholder test")
        assert detect_pii_in_value(cleaned) == []

    mixed_nested = strip_pii_spans("<MASKED_alice@example.comPII>")
    assert not has_redacted_placeholder(mixed_nested)
    ensure_no_redacted_placeholder(mixed_nested, context="Mixed nested placeholder test")
    assert detect_pii_in_value(mixed_nested) == []


def test_ensure_no_redacted_placeholder_raises() -> None:
    """ensure_no_redacted_placeholder fails closed with REDACTED_PLACEHOLDER_LEAK."""
    for bad in (f"Note {MASKED_PII_TOKEN}", "Note [REDACTED_PII]", "Note [REDACTED_SECRET]"):
        assert has_redacted_placeholder(bad) is True
        with pytest.raises(OpenDVError) as exc_info:
            ensure_no_redacted_placeholder(bad, context="Rerank prompt")
        assert exc_info.value.error_code == ErrorCode.REDACTED_PLACEHOLDER_LEAK


def test_detect_pii_in_value_exempt_high_entropy_for_reference_packs() -> None:
    """exempt_high_entropy=True avoids false positives on dense reference prose."""
    dense_ref = "InterestRateBenchmark(SOFR/ESTR/SONIA_overnight_rate_v2.0+USD/EUR)"
    # High-entropy dense symbol string triggers when exempt_high_entropy=False
    assert "HIGH_ENTROPY_SECRET" in detect_pii_in_value(dense_ref, exempt_high_entropy=False)
    # Exempted for verified reference-pack definitions, while real PII still triggers
    assert detect_pii_in_value(dense_ref, exempt_high_entropy=True) == []
    assert "SSN" in detect_pii_in_value(
        f"{dense_ref} 123-45-6789", exempt_high_entropy=True
    )
    assert "EMAIL" in detect_pii_in_value(
        f"{dense_ref} user@example.com", exempt_high_entropy=True
    )


def test_rerank_column_packet_enforces_empty_enums_and_pii_suppression() -> None:
    """RerankColumnPacket rejects non-empty observed_enum_values and accepts suppressed shapes."""
    suppressed_shape = ColumnShapeStats(
        profiled_non_null_count=20,
        distinct_ratio=0.10,
        length_min=None,
        length_max=None,
        numeric_min=None,
        numeric_max=None,
        looks_like_enum=True,
        observed_enum_values=[],
        pii_suppressed=True,
    )
    profile = InferredValueProfile(
        observed_sample_count=5,
        non_null_count=5,
        masked_count=5,
        declared_logical_type="UTF8_STRING",
        inferred_logical_type="UTF8_STRING",
        type_confidence=1.0,
        pii_tags=["PII_COLUMN:ABA"],
        shape=suppressed_shape,
    )
    pkt = RerankColumnPacket(
        column_name="counterparty_aba_raw",
        data_type="VARCHAR",
        steward_note=None,
        inferred_value_profile=profile,
        pii_tags=["PII_COLUMN:ABA"],
        candidates=[
            RerankCandidateSummary(
                index=0,
                label="Routing Number",
                entry_kind="datatype_property",
                definition="US ABA routing code",
                semantic_hint="identifier",
                prior_score=0.80,
            )
        ],
    )
    assert pkt.inferred_value_profile.shape.observed_enum_values == []

    # Non-empty observed_enum_values is rejected even when pii_suppressed=False
    enum_shape = ColumnShapeStats(
        profiled_non_null_count=20,
        distinct_ratio=0.10,
        looks_like_enum=True,
        observed_enum_values=["USD", "EUR"],
        pii_suppressed=False,
    )
    enum_profile = InferredValueProfile(
        observed_sample_count=20,
        non_null_count=20,
        masked_count=0,
        declared_logical_type="UTF8_STRING",
        inferred_logical_type="UTF8_STRING",
        type_confidence=1.0,
        pii_tags=[],
        shape=enum_shape,
    )
    with pytest.raises(ValidationError):
        RerankColumnPacket(
            column_name="ccy",
            data_type="VARCHAR",
            inferred_value_profile=enum_profile,
            pii_tags=[],
            candidates=[],
        )


def test_masked_zero_n_eff_blocks_matched_promotion() -> None:
    """Externally pre-masked n_eff=0 with masked_count>0 forces AMBIGUOUS(unverified_masked_values)."""
    ctx = ColumnCalibrationContext(
        column_name="counterparty_aba_raw",
        n_eff=0,
        masked_count=12,
        has_shape_or_declared_type=True,
    )
    # Realistic pre-masked column: generic string shape + missing surface rule widens CI (L1 < 0.45)
    cand_realistic = CandidateChannelInputs(
        entry_id="https://spec.edmcouncil.org/fibo/RoutingNumber",
        label="counterparty_aba_raw",
        entry_kind="datatype_property",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(archetype="identifier"),
        lex_score=0.95,
        shape_range_score=0.20,
        has_surface_rule=True,
    )
    outcome_low = select_and_calibrate(
        ctx,
        [cand_realistic],
        CalibrationParams.analytic_defaults(),
        GroundingPolicy(mode="deterministic_only", skip_reason="unit test"),
    )
    assert outcome_low.status == "AMBIGUOUS"
    assert outcome_low.review_reason == "unverified_masked_values"
    assert outcome_low.fast_path_eligible is False
    assert outcome_low.choices
    assert outcome_low.choices[0].confidence.lower < 0.45

    # Even if strong shape pushes L1 >= 0.45, the explicit n_eff > 0 gate still blocks MATCHED
    cand_strong = CandidateChannelInputs(
        entry_id="https://spec.edmcouncil.org/fibo/RoutingNumber",
        label="counterparty_aba_raw",
        entry_kind="datatype_property",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(archetype="identifier"),
        lex_score=0.95,
        shape_range_score=0.90,
    )
    outcome_strong = select_and_calibrate(
        ctx,
        [cand_strong],
        CalibrationParams.analytic_defaults(),
        GroundingPolicy(mode="deterministic_only", skip_reason="unit test"),
    )
    assert outcome_strong.status == "AMBIGUOUS"
    assert outcome_strong.review_reason == "unverified_masked_values"
    assert outcome_strong.fast_path_eligible is False
