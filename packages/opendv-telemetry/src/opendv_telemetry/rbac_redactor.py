"""5-Tier RBAC Data Classification & Redaction Engine for Open-DV Telemetry.

Conforms to docs/06-explainability-and-observability-spec.md (§1.1)
and schemas/vault-observability-event.schema.json.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Collection
from enum import StrEnum
from typing import Any

from opendv_core.models.observability import VaultObservabilityEvent

RE_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
RE_EIN = re.compile(r"\b\d{2}-\d{7}\b")
RE_ABA = re.compile(r"\b\d{9}\b")
RE_MNPI_FIGURES = re.compile(
    r"\b\d+\.\d+x\b|\$\d+[\d,]*(?:\.\d+)?[KMBTkmbt]?\b(?!\.\d)|\b\d+(?:\.\d+)?M\b"
)


class RbacRole(StrEnum):
    """5-Tier RBAC Roles governing telemetry event access and redaction."""

    PUBLIC = "PUBLIC"
    INTERNAL_MODELER = "INTERNAL_MODELER"
    CONFIDENTIAL_ANALYST = "CONFIDENTIAL_ANALYST"
    DATA_STEWARD = "DATA_STEWARD"  # Standard alias for CONFIDENTIAL_ANALYST
    AUDITOR_FINANCE = "AUDITOR_FINANCE"
    PII_STEWARD = "PII_STEWARD"
    RESTRICTED_PII_CUSTODIAN = "RESTRICTED_PII_CUSTODIAN"
    PRIVILEGED_CUSTODIAN = "PRIVILEGED_CUSTODIAN"


ROLE_CLEARANCE_LEVELS: dict[RbacRole | str, frozenset[str]] = {
    RbacRole.PUBLIC: frozenset({"PUBLIC"}),
    RbacRole.INTERNAL_MODELER: frozenset({"PUBLIC", "INTERNAL"}),
    RbacRole.CONFIDENTIAL_ANALYST: frozenset({"PUBLIC", "INTERNAL", "CONFIDENTIAL"}),
    RbacRole.DATA_STEWARD: frozenset({"PUBLIC", "INTERNAL", "CONFIDENTIAL"}),
    RbacRole.AUDITOR_FINANCE: frozenset({"PUBLIC", "INTERNAL", "CONFIDENTIAL", "MNPI"}),
    RbacRole.PII_STEWARD: frozenset({"PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED_PII"}),
    RbacRole.RESTRICTED_PII_CUSTODIAN: frozenset(
        {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "MNPI", "RESTRICTED_PII"}
    ),
    RbacRole.PRIVILEGED_CUSTODIAN: frozenset(
        {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "MNPI", "RESTRICTED_PII"}
    ),
    # Additional domain aliases recognized in Open-DV
    "MNPI_CLEARED_STEWARD": frozenset({"PUBLIC", "INTERNAL", "CONFIDENTIAL", "MNPI"}),
    "LP_CONFIDENTIAL_STEWARD": frozenset({"PUBLIC", "INTERNAL", "CONFIDENTIAL"}),
    "COMPLIANCE_AUDITOR": frozenset({"PUBLIC", "INTERNAL"}),
}


def resolve_cleared_tiers(
    role_or_roles: RbacRole | str | Collection[RbacRole | str] | None,
) -> frozenset[str]:
    """Resolve the union of cleared data classification tiers for given role(s)."""
    if role_or_roles is None:
        return frozenset({"PUBLIC"})

    if isinstance(role_or_roles, (str, RbacRole)):
        roles_list: list[RbacRole | str] = [role_or_roles]
    else:
        roles_list = list(role_or_roles)

    if not roles_list:
        return frozenset({"PUBLIC"})

    cleared: set[str] = {"PUBLIC"}
    for r in roles_list:
        role_key: RbacRole | str
        if isinstance(r, RbacRole):
            role_key = r
        else:
            r_str = str(r).strip()
            try:
                role_key = RbacRole(r_str)
            except ValueError:
                role_key = r_str

        if role_key in ROLE_CLEARANCE_LEVELS:
            cleared.update(ROLE_CLEARANCE_LEVELS[role_key])
        else:
            # Fallback for unknown role: treat as PUBLIC
            cleared.update({"PUBLIC"})

    return frozenset(cleared)


def _mask_pii_recursive(val: Any, key_name: str = "") -> Any:
    """Recursively mask SSN, EIN, ABA routing numbers, and sensitive key names."""
    if isinstance(val, dict):
        return {k: _mask_pii_recursive(v, k) for k, v in val.items()}
    if isinstance(val, list):
        return [_mask_pii_recursive(item, key_name) for item in val]
    if isinstance(val, str):
        if any(
            term in key_name.lower()
            for term in ["aba", "routing", "tax_id", "wire_iban", "ssn", "ein", "bank_account"]
        ):
            return "[REDACTED:RESTRICTED_PII]"
        masked = RE_SSN.sub("[REDACTED:RESTRICTED_PII]", val)
        masked = RE_EIN.sub("[REDACTED:RESTRICTED_PII]", masked)
        masked = RE_ABA.sub(
            lambda m: (
                m.group(0)
                if m.group(0).startswith(("2026", "1900", "0000"))
                else "[REDACTED:RESTRICTED_PII]"
            ),
            masked,
        )
        return masked
    return val


def redact_event_for_role(
    event: dict[str, Any] | VaultObservabilityEvent,
    role: RbacRole | str | Collection[RbacRole | str] = RbacRole.INTERNAL_MODELER,
) -> dict[str, Any]:
    """Redact sensitive fields in a telemetry event according to the caller's RBAC clearance.

    Enforces 5-Tier Data Classification & Redaction:
    1. PUBLIC / INTERNAL: General metadata and unmasked keys.
    2. CONFIDENTIAL: Masks individual LP names and business keys in semantic hash pills.
    3. MNPI: Masks pre-release valuation multiples, fair values, and financial figures.
    4. RESTRICTED_PII: Masks SSN, EIN, and wire routing tokens across all payloads.

    Returns a deep-copied, schema-valid event dictionary.
    """
    if isinstance(event, VaultObservabilityEvent):
        cloned: dict[str, Any] = event.model_dump(mode="json", exclude_none=True)
    else:
        cloned = copy.deepcopy(event)

    cleared_tiers = resolve_cleared_tiers(role)
    tier = str(cloned.get("data_classification") or "PUBLIC")

    # 1. Tier 5 (RESTRICTED_PII): Mask unless role clears RESTRICTED_PII
    if "RESTRICTED_PII" not in cleared_tiers:
        cloned = _mask_pii_recursive(cloned)

    # 2. Tier 4 (MNPI): Mask valuation figures if caller lacks MNPI clearance
    if "MNPI" not in cleared_tiers and tier in {"MNPI", "RESTRICTED_PII"}:
        l1 = cloned.get("layer1_story_banner")
        if isinstance(l1, dict):
            if "headline" in l1 and isinstance(l1["headline"], str):
                l1["headline"] = RE_MNPI_FIGURES.sub("[REDACTED:MNPI]", l1["headline"])
            if "business_impact" in l1 and isinstance(l1["business_impact"], str):
                l1["business_impact"] = RE_MNPI_FIGURES.sub(
                    "[REDACTED:MNPI]", l1["business_impact"]
                )

        l2 = cloned.get("layer2_visual_payload")
        if isinstance(l2, dict):
            metrics = l2.get("summary_metrics")
            if isinstance(metrics, dict):
                for k in list(metrics.keys()):
                    if any(
                        term in k.lower()
                        for term in ["valuation", "multiple", "ev_", "ebitda", "fair_value"]
                    ):
                        metrics[k] = "[REDACTED:MNPI]"

    # 3. Tier 3 (CONFIDENTIAL): Mask individual LP identities if caller lacks CONFIDENTIAL
    if "CONFIDENTIAL" not in cleared_tiers and tier in {"CONFIDENTIAL", "MNPI", "RESTRICTED_PII"}:
        l2 = cloned.get("layer2_visual_payload")
        if isinstance(l2, dict):
            pills = l2.get("semantic_hash_pills")
            if isinstance(pills, list):
                for pill in pills:
                    if isinstance(pill, dict):
                        digest = str(pill.get("full_sha256_hex", ""))
                        short_hex = (
                            f"{digest[:4]}..{digest[-4:]}" if len(digest) >= 8 else "REDACTED"
                        )
                        kind = "HK"
                        if "pill_label" in pill and isinstance(pill["pill_label"], str):
                            raw_kind = pill["pill_label"].split(":")[0].lstrip("[")
                            if raw_kind in ("HK", "LHK", "HD", "GHOST", "ZERO-KEY", "ERR-KEY"):
                                kind = raw_kind

                        pill["business_key"] = "[REDACTED:CONFIDENTIAL]"
                        pill["entity_label"] = "[REDACTED:CONFIDENTIAL]"
                        pill["pill_label"] = f"[{kind}: {short_hex} | [REDACTED:CONFIDENTIAL]]"

    # 4. Overall classification check: If entire event tier is not cleared
    if tier not in cleared_tiers:
        stage_id = cloned.get("stage_id", "UNKNOWN_STAGE")
        role_label = str(role.value) if isinstance(role, RbacRole) else str(role)

        l1 = cloned.get("layer1_story_banner")
        if isinstance(l1, dict):
            current_hl = l1.get("headline", "")
            if "[REDACTED" not in current_hl:
                new_hl = f"[REDACTED {tier}] Stage {stage_id} masked for role '{role_label}'."
                l1["headline"] = new_hl[:140]
            current_bi = l1.get("business_impact", "")
            if "[REDACTED" not in current_bi:
                new_bi = f"Sensitive {tier} telemetry payload masked for role '{role_label}'."
                l1["business_impact"] = new_bi[:200]

        # Mask Layer 3 formal proof details
        proof = cloned.get("layer3_formal_proof")
        if isinstance(proof, dict):
            for k in list(proof.keys()):
                if k != "algorithm_ref":
                    proof[k] = f"[REDACTED {tier}]"

    l1_final = cloned.get("layer1_story_banner")
    if isinstance(l1_final, dict):
        if isinstance(l1_final.get("headline"), str) and len(l1_final["headline"]) > 140:
            l1_final["headline"] = l1_final["headline"][:137] + "..."
        if (
            isinstance(l1_final.get("business_impact"), str)
            and len(l1_final["business_impact"]) > 200
        ):
            l1_final["business_impact"] = l1_final["business_impact"][:197] + "..."

    return cloned


def redact_telemetry_event(
    event: dict[str, Any] | VaultObservabilityEvent,
    viewer_roles: list[str] | None = None,
) -> dict[str, Any]:
    """Compatibility adapter matching test_observability_and_redaction.py signature."""
    effective_role: RbacRole | list[str] = (
        viewer_roles if viewer_roles else RbacRole.INTERNAL_MODELER
    )
    return redact_event_for_role(event, role=effective_role)


__all__ = [
    "ROLE_CLEARANCE_LEVELS",
    "RbacRole",
    "redact_event_for_role",
    "redact_telemetry_event",
    "resolve_cleared_tiers",
]
