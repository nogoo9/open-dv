"""Sample data redactor for Ontology Recommender inputs (Decision D9, ONT-05).

Enforces RBAC-level masking of PII (Personally Identifiable Information) and
MNPI (Material Non-Public Information) before raw bronze records reach external
or independent ontology recommenders.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from opendv_telemetry.rbac_redactor import (
    RE_ABA,
    RE_EIN,
    RE_MNPI_FIGURES,
    RE_SSN,
    RbacRole,
    resolve_cleared_tiers,
)

RE_CREDIT_CARD = re.compile(r"\b(?:\d{4}[- ]?){3}\d{4}\b")
RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
RE_PHONE = re.compile(r"\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b")

_PII_KEY_TOKENS = frozenset(
    {
        "ssn",
        "ein",
        "tax_id",
        "aba",
        "routing",
        "bank_account",
        "account_number",
        "wire_iban",
        "iban",
        "credit_card",
        "card_number",
        "passport",
        "driver_license",
        "password",
        "secret",
        "private_key",
        "name",
        "first_name",
        "last_name",
        "full_name",
        "dob",
        "birth",
        "date_of_birth",
        "birth_date",
        "address",
        "street",
        "zip",
        "postal",
        "postal_code",
        "national_id",
        "phone",
        "telephone",
        "email",
    }
)

_MNPI_KEY_TOKENS = frozenset(
    {
        "valuation",
        "ebitda",
        "pre_money",
        "post_money",
        "carried_interest",
        "lp_return",
        "irr",
        "moic",
        "deal_multiple",
        "confidential_ebitda",
        "mnpi",
    }
)


def is_pii_key(key: str) -> bool:
    """Return True if column/key name indicates sensitive PII."""
    normalized = key.lower().strip()
    return any(token in normalized for token in _PII_KEY_TOKENS)


def is_mnpi_key(key: str) -> bool:
    """Return True if column/key name indicates confidential financial MNPI."""
    normalized = key.lower().strip()
    return any(token in normalized for token in _MNPI_KEY_TOKENS)


def redact_sample_value(
    val: Any,
    key_name: str = "",
    clearance_level: str | RbacRole = "INTERNAL",
) -> Any:
    """Deterministically redact a single value given RBAC clearance and key context."""
    if val is None:
        return None

    cleared_tiers = resolve_cleared_tiers(clearance_level)
    has_pii_clearance = "RESTRICTED_PII" in cleared_tiers
    has_mnpi_clearance = "MNPI" in cleared_tiers

    # Key-name based redaction
    if not has_pii_clearance and is_pii_key(key_name):
        return "[REDACTED:RESTRICTED_PII]"

    if not has_mnpi_clearance and is_mnpi_key(key_name):
        return "[REDACTED:MNPI]"

    # Pattern-based redaction on string content
    if isinstance(val, str):
        masked = val
        if not has_pii_clearance:
            masked = RE_SSN.sub("[REDACTED:RESTRICTED_PII]", masked)
            masked = RE_EIN.sub("[REDACTED:RESTRICTED_PII]", masked)
            masked = RE_CREDIT_CARD.sub("[REDACTED:RESTRICTED_PII]", masked)
            masked = RE_ABA.sub(
                lambda m: (
                    m.group(0)
                    if m.group(0).startswith(("2026", "1900", "0000"))
                    else "[REDACTED:RESTRICTED_PII]"
                ),
                masked,
            )
            masked = RE_EMAIL.sub("[REDACTED:PII]", masked)
            masked = RE_PHONE.sub("[REDACTED:PII]", masked)

        if not has_mnpi_clearance:
            masked = RE_MNPI_FIGURES.sub("[REDACTED:MNPI]", masked)

        return masked

    # Numeric masking if key indicates MNPI or PII
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if not has_mnpi_clearance and is_mnpi_key(key_name):
            return "[REDACTED:MNPI]"
        if not has_pii_clearance and is_pii_key(key_name):
            return "[REDACTED:RESTRICTED_PII]"

    return val


def _redact_recursive(
    data: Any,
    key_name: str,
    clearance_level: str | RbacRole,
    max_str_len: int,
) -> Any:
    """Recursively traverse nested objects, applying redaction and length bounds."""
    if isinstance(data, dict):
        return {
            str(k): _redact_recursive(v, str(k), clearance_level, max_str_len)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_redact_recursive(item, key_name, clearance_level, max_str_len) for item in data]

    redacted = redact_sample_value(data, key_name=key_name, clearance_level=clearance_level)
    if (
        isinstance(redacted, str)
        and len(redacted) > max_str_len
        and not redacted.startswith("[REDACTED:")
    ):
        return f"{redacted[:max_str_len]}...[TRUNCATED]"
    return redacted


def redact_sample_data(
    samples: Any,
    clearance_level: str | RbacRole = "INTERNAL",
    *,
    max_rows_per_table: int = 10,
    max_str_len: int = 500,
    max_tables: int = 50,
) -> Any:
    """Redact and bound sample datasets before passing to external recommenders.

    - Masks PII/MNPI according to RBAC clearance (default INTERNAL).
    - Enforces row-count bounding per table to avoid oversized prompt contexts.
    - Truncates oversized string values to max_str_len.
    """
    if samples is None:
        return None

    cloned = copy.deepcopy(samples)

    if isinstance(cloned, dict):
        result_dict: dict[str, Any] = {}
        for table_count, (table_name, rows) in enumerate(cloned.items()):
            if table_count >= max_tables:
                break
            if isinstance(rows, list):
                bounded_rows = rows[:max_rows_per_table]
                result_dict[table_name] = [
                    _redact_recursive(r, "", clearance_level, max_str_len) for r in bounded_rows
                ]
            else:
                result_dict[table_name] = _redact_recursive(
                    rows, table_name, clearance_level, max_str_len
                )
        return result_dict

    if isinstance(cloned, list):
        bounded_list = cloned[:max_rows_per_table]
        return [_redact_recursive(item, "", clearance_level, max_str_len) for item in bounded_list]

    return _redact_recursive(cloned, "", clearance_level, max_str_len)


def redact_recommendation_request(
    req: Any,
    clearance_level: str | RbacRole = "INTERNAL",
) -> Any:
    """Redact samples, context documents, and profiles on an OntologyRecommendationRequest."""
    if not hasattr(req, "model_copy"):
        return req

    updates: dict[str, Any] = {}
    if getattr(req, "samples", None) is not None:
        updates["samples"] = redact_sample_data(req.samples, clearance_level=clearance_level)

    if getattr(req, "context_documents", None) is not None:
        docs = req.context_documents
        if isinstance(docs, list):
            updates["context_documents"] = [
                redact_sample_data(d, clearance_level=clearance_level) for d in docs
            ]
        elif isinstance(docs, dict):
            updates["context_documents"] = redact_sample_data(docs, clearance_level=clearance_level)

    if getattr(req, "profiles", None) is not None:
        updates["profiles"] = redact_sample_data(req.profiles, clearance_level=clearance_level)

    return req.model_copy(update=updates) if updates else req


__all__ = [
    "is_mnpi_key",
    "is_pii_key",
    "redact_recommendation_request",
    "redact_sample_data",
    "redact_sample_value",
]
