"""Semantic Hash Pill formatting utilities for Open-DV Layer 2 visual canvases.

Conforms to docs/06-explainability-and-observability-spec.md (§2.2, §2.4)
and schemas/vault-observability-event.schema.json.
"""

from __future__ import annotations

import re
from typing import Final

from opendv_core.constants import (
    GLOBAL_BKCC,
    ZERO_KEY_ERROR_UPPER,
    ZERO_KEY_UNKNOWN,
)

HEX_64_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9A-Fa-f]{64}$")


def _normalize_sha256(raw_hex: str, default: str = ZERO_KEY_UNKNOWN) -> str:
    """Normalize and validate a 64-character SHA-256 hexadecimal string."""
    cleaned = (raw_hex or default).strip().upper()
    if not HEX_64_PATTERN.match(cleaned):
        msg = f"Invalid SHA-256 hex string: '{raw_hex}' (expected 64 hex characters)"
        raise ValueError(msg)
    return cleaned


def format_semantic_hash_pill(
    kind: str,
    full_sha256_hex: str = "",
    bkcc_prefix: str = GLOBAL_BKCC,
    business_key: str = "",
    entity_label: str = "",
) -> dict[str, str]:
    """Format a Layer-2 Semantic Hash Pill dictionary for visual canvases.

    Renders human-readable badges preventing 64-hex cognitive fatigue:
    - Hub: `[HK: 5D15..0AE4 | GLOBAL:LP-101 CalPERS]`
    - Link: `[LHK: EB15..7C4E | Fund IV <-> CalPERS]`
    - Hash Diff: `[HD: 1234..ABCD | sat_lp_commitment_fx]`
    - Ghost Record: `[GHOST: 0000..0000 | SYSTEM_GHOST (1900-01-01)]`
    - Zero Key (Unknown): `[ZERO-KEY: 0000..0000 | UNKNOWN (-1)]`
    - Error Key: `[ERR-KEY: FFFF..FFFF | ERROR (-2)]`

    Returns a dict conforming to `SemanticHashPill` in `vault-observability-event.schema.json`.
    """
    kind_upper = kind.strip().upper()

    if kind_upper == "GHOST":
        digest = _normalize_sha256(full_sha256_hex, default=ZERO_KEY_UNKNOWN)
        bkcc = bkcc_prefix if bkcc_prefix and bkcc_prefix != GLOBAL_BKCC else "SYSTEM_GHOST"
        bkey = business_key or "1900-01-01"
        label = entity_label or "SYSTEM_GHOST (1900-01-01)"
        short_hex = f"{digest[:4]}..{digest[-4:]}"
        pill_label = f"[GHOST: {short_hex} | {label}]"
        return {
            "pill_label": pill_label,
            "full_sha256_hex": digest,
            "bkcc_prefix": bkcc,
            "business_key": bkey,
            "entity_label": label,
        }

    if kind_upper in ("ZERO-KEY", "ZEROKEY"):
        digest = _normalize_sha256(full_sha256_hex, default=ZERO_KEY_UNKNOWN)
        bkcc = bkcc_prefix if bkcc_prefix and bkcc_prefix != GLOBAL_BKCC else "SYSTEM"
        bkey = business_key or "-1"
        label = entity_label or "UNKNOWN (-1)"
        short_hex = f"{digest[:4]}..{digest[-4:]}"
        pill_label = f"[ZERO-KEY: {short_hex} | {label}]"
        return {
            "pill_label": pill_label,
            "full_sha256_hex": digest,
            "bkcc_prefix": bkcc,
            "business_key": bkey,
            "entity_label": label,
        }

    if kind_upper in ("ERR-KEY", "ERRKEY", "ERROR-KEY"):
        digest = _normalize_sha256(full_sha256_hex, default=ZERO_KEY_ERROR_UPPER)
        bkcc = bkcc_prefix if bkcc_prefix and bkcc_prefix != GLOBAL_BKCC else "SYSTEM"
        bkey = business_key or "-2"
        label = entity_label or "ERROR (-2)"
        short_hex = f"{digest[:4]}..{digest[-4:]}"
        pill_label = f"[ERR-KEY: {short_hex} | {label}]"
        return {
            "pill_label": pill_label,
            "full_sha256_hex": digest,
            "bkcc_prefix": bkcc,
            "business_key": bkey,
            "entity_label": label,
        }

    digest = _normalize_sha256(full_sha256_hex)
    short_hex = f"{digest[:4]}..{digest[-4:]}"

    if kind_upper == "LHK":
        if "<->" in entity_label and not business_key:
            inner = entity_label
            bkey = entity_label
            label = entity_label
        elif "<->" in business_key:
            inner = f"{bkcc_prefix}:{business_key}" if bkcc_prefix != GLOBAL_BKCC else business_key
            bkey = business_key
            label = entity_label or business_key
        else:
            mid = f"{bkcc_prefix}:{business_key}" if business_key else bkcc_prefix
            label_suffix = f" {entity_label}" if entity_label else ""
            inner = f"{mid}{label_suffix}".strip()
            bkey = business_key
            label = entity_label

        pill_label = f"[LHK: {short_hex} | {inner}]"
        return {
            "pill_label": pill_label,
            "full_sha256_hex": digest,
            "bkcc_prefix": bkcc_prefix,
            "business_key": bkey,
            "entity_label": label,
        }

    if kind_upper == "HD":
        if (entity_label and business_key and entity_label == business_key) or (
            entity_label and not business_key
        ):
            inner = entity_label
        elif business_key and not entity_label:
            inner = business_key
        elif business_key and entity_label:
            inner = f"{business_key} {entity_label}".strip()
        else:
            inner = "hash_diff"
        pill_label = f"[HD: {short_hex} | {inner}]"
        return {
            "pill_label": pill_label,
            "full_sha256_hex": digest,
            "bkcc_prefix": bkcc_prefix,
            "business_key": business_key or inner,
            "entity_label": entity_label or inner,
        }

    # Standard / Default (e.g. "HK")
    mid = f"{bkcc_prefix}:{business_key}" if business_key else bkcc_prefix
    label_suffix = f" {entity_label}" if entity_label else ""
    pill_label = f"[{kind_upper}: {short_hex} | {mid}{label_suffix}]"
    return {
        "pill_label": pill_label,
        "full_sha256_hex": digest,
        "bkcc_prefix": bkcc_prefix,
        "business_key": business_key,
        "entity_label": entity_label,
    }


def format_ghost_pill(
    full_sha256_hex: str = ZERO_KEY_UNKNOWN,
    label: str = "SYSTEM_GHOST (1900-01-01)",
) -> dict[str, str]:
    """Convenience helper to format a System Ghost Record pill."""
    return format_semantic_hash_pill(
        kind="GHOST",
        full_sha256_hex=full_sha256_hex,
        bkcc_prefix="SYSTEM_GHOST",
        business_key="1900-01-01",
        entity_label=label,
    )


def format_zero_key_pill(
    full_sha256_hex: str = ZERO_KEY_UNKNOWN,
    label: str = "UNKNOWN (-1)",
) -> dict[str, str]:
    """Convenience helper to format an Unknown Zero Key pill."""
    return format_semantic_hash_pill(
        kind="ZERO-KEY",
        full_sha256_hex=full_sha256_hex,
        bkcc_prefix="SYSTEM",
        business_key="-1",
        entity_label=label,
    )


def format_error_key_pill(
    full_sha256_hex: str = ZERO_KEY_ERROR_UPPER,
    label: str = "ERROR (-2)",
) -> dict[str, str]:
    """Convenience helper to format an Error Zero Key pill."""
    return format_semantic_hash_pill(
        kind="ERR-KEY",
        full_sha256_hex=full_sha256_hex,
        bkcc_prefix="SYSTEM",
        business_key="-2",
        entity_label=label,
    )


def format_hub_pill(
    full_sha256_hex: str,
    bkcc_prefix: str,
    business_key: str,
    entity_label: str,
) -> dict[str, str]:
    """Convenience helper to format a Hub Hash Key pill."""
    return format_semantic_hash_pill(
        kind="HK",
        full_sha256_hex=full_sha256_hex,
        bkcc_prefix=bkcc_prefix,
        business_key=business_key,
        entity_label=entity_label,
    )


def format_link_pill(
    full_sha256_hex: str,
    link_label: str,
    bkcc_prefix: str = GLOBAL_BKCC,
    business_key: str = "",
) -> dict[str, str]:
    """Convenience helper to format a Link Hash Key pill."""
    return format_semantic_hash_pill(
        kind="LHK",
        full_sha256_hex=full_sha256_hex,
        bkcc_prefix=bkcc_prefix,
        business_key=business_key or link_label,
        entity_label=link_label,
    )


def format_hash_diff_pill(
    full_sha256_hex: str,
    satellite_name: str,
) -> dict[str, str]:
    """Convenience helper to format a Satellite Hash Diff pill."""
    return format_semantic_hash_pill(
        kind="HD",
        full_sha256_hex=full_sha256_hex,
        bkcc_prefix="SYSTEM",
        business_key=satellite_name,
        entity_label=satellite_name,
    )


def format_compact_hash_pill(
    entity_label: str,
    business_keys: list[str] | str,
    hex_digest: str,
) -> str:
    """Format a compact Semantic Hash Pill badge: `<EntityLabel>(<BK_Summary>) · #<hex[0:6]>`."""
    clean_label = entity_label.strip() if entity_label else "Entity"

    if isinstance(business_keys, str):
        bk_list = [business_keys.strip()] if business_keys.strip() else []
    elif isinstance(business_keys, (list, tuple)):
        bk_list = [str(k).strip() for k in business_keys if k is not None and str(k).strip()]
    else:
        bk_list = []

    bk_summary = ", ".join(bk_list)
    digest_clean = (hex_digest or "").strip().lower()
    short_hex = digest_clean[:6] if len(digest_clean) >= 6 else digest_clean.ljust(6, "0")

    return f"{clean_label}({bk_summary}) · #{short_hex}"


__all__ = [
    "HEX_64_PATTERN",
    "format_compact_hash_pill",
    "format_error_key_pill",
    "format_ghost_pill",
    "format_hash_diff_pill",
    "format_hub_pill",
    "format_link_pill",
    "format_semantic_hash_pill",
    "format_zero_key_pill",
]
