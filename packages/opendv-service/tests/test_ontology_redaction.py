"""Unit tests for sample data redaction engine."""

from __future__ import annotations

from opendv_service.ontology.redaction import (
    is_mnpi_key,
    is_pii_key,
    redact_sample_data,
    redact_sample_value,
)


def test_is_pii_and_mnpi_key() -> None:
    assert is_pii_key("customer_ssn")
    assert is_pii_key("tax_id")
    assert is_pii_key("bank_account_num")
    assert not is_pii_key("customer_city")

    assert is_mnpi_key("portfolio_valuation")
    assert is_mnpi_key("confidential_ebitda")
    assert not is_mnpi_key("order_date")


def test_pii_masking_at_internal_clearance() -> None:
    # SSN
    val = redact_sample_value(
        "Customer SSN is 123-45-6789", key_name="note", clearance_level="INTERNAL"
    )
    assert "[REDACTED:RESTRICTED_PII]" in val
    assert "123-45-6789" not in val

    # EIN
    val2 = redact_sample_value(
        "Company EIN: 12-3456789", key_name="note", clearance_level="INTERNAL"
    )
    assert "[REDACTED:RESTRICTED_PII]" in val2

    # Credit card
    val3 = redact_sample_value(
        "Paid via 4111 2222 3333 4444", key_name="note", clearance_level="INTERNAL"
    )
    assert "[REDACTED:RESTRICTED_PII]" in val3

    # Email and phone
    val4 = redact_sample_value(
        "Contact john.doe@example.com or 555-123-4567",
        key_name="contact",
        clearance_level="INTERNAL",
    )
    assert "[REDACTED:PII]" in val4


def test_mnpi_masking_at_internal_clearance() -> None:
    val = redact_sample_value(
        "Target valuation was $500M at 12.5x multiple",
        key_name="brief",
        clearance_level="INTERNAL",
    )
    assert "[REDACTED:MNPI]" in val
    assert "$500M" not in val
    assert "12.5x" not in val


def test_mnpi_clearance_for_auditor_finance() -> None:
    # At AUDITOR_FINANCE, MNPI figures are preserved, but PII is still masked
    val = redact_sample_value(
        "Target valuation was $500M, SSN 123-45-6789",
        key_name="audit",
        clearance_level="AUDITOR_FINANCE",
    )
    assert "$500M" in val
    assert "[REDACTED:RESTRICTED_PII]" in val


def test_full_clearance_for_privileged_custodian() -> None:
    val = redact_sample_value(
        "Target valuation was $500M, SSN 123-45-6789",
        key_name="custodian",
        clearance_level="PRIVILEGED_CUSTODIAN",
    )
    assert "$500M" in val
    assert "123-45-6789" in val


def test_sample_dataset_bounding_and_redaction() -> None:
    samples = {
        "raw_suppliers": [
            {"supplier_id": f"s_{i}", "ssn": "123-45-6789", "valuation": 1000000}
            for i in range(25)
        ]
    }
    redacted = redact_sample_data(
        samples,
        clearance_level="INTERNAL",
        max_rows_per_table=5,
        max_str_len=20,
    )

    # Row count bounding
    assert len(redacted["raw_suppliers"]) == 5

    # Redaction
    for row in redacted["raw_suppliers"]:
        assert row["ssn"] == "[REDACTED:RESTRICTED_PII]"
        assert row["valuation"] == "[REDACTED:MNPI]"


def test_string_truncation_bound() -> None:
    long_text = "A" * 1000
    redacted = redact_sample_data([{"notes": long_text}], max_str_len=50)
    assert len(redacted[0]["notes"]) <= 70
    assert "...[TRUNCATED]" in redacted[0]["notes"]
