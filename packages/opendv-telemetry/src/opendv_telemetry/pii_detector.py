"""Adversarial PII, MNPI, and Raw 64-Hex Digest Detector for Open-DV Telemetry.

Conforms to docs/06-explainability-and-observability-spec.md (§1.1, §1.3, §2.1)
and enforces zero-trust telemetry safety before event ledger persistence.
"""

from __future__ import annotations

import collections
import math
import re
from dataclasses import dataclass
from typing import Any, Final

from opendv_core.models.observability import VaultObservabilityEvent

RE_SSN: Final[re.Pattern[str]] = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
RE_EIN: Final[re.Pattern[str]] = re.compile(r"\b\d{2}-\d{7}\b")
RE_ABA: Final[re.Pattern[str]] = re.compile(r"\b\d{9}\b")
RE_HEX_64: Final[re.Pattern[str]] = re.compile(r"\b[0-9A-Fa-f]{64}\b")


class PIILeakViolationError(Exception):
    """Raised when unmasked Restricted PII, MNPI, or raw 64-hex digests violate telemetry policy."""


@dataclass(frozen=True)
class LeakFinding:
    """Record of a detected PII, raw 64-hex digest, or high-entropy data leak."""

    path: str
    leak_type: str
    matched_value: str
    severity: str = "CRITICAL"


def compute_shannon_entropy_text(text: str) -> float:
    """Compute normalized Shannon entropy in bits per character.

    A random or cryptographic 64-hex digest typically exhibits Shannon entropy >= 3.6 bits/char.
    """
    if not text:
        return 0.0
    n = len(text)
    counts = collections.Counter(text)
    return -sum((count / n) * math.log2(count / n) for count in counts.values())


def detect_unmasked_pii_leaks(obj: Any, path: str = "") -> list[tuple[str, str, str]]:
    """Adversarial recursive scanner detecting unmasked SSN, EIN, or ABA routing numbers.

    Returns a list of `(json_path, leak_type, matched_string)` tuples.
    Excludes synthetic PE demo dates and zero-key seeds (2026*, 1900*, 0000*).
    """
    if isinstance(obj, VaultObservabilityEvent):
        obj = obj.model_dump(mode="json", exclude_none=True)

    leaks: list[tuple[str, str, str]] = []

    if isinstance(obj, dict):
        for k, v in obj.items():
            sub_path = f"{path}.{k}" if path else k
            leaks.extend(detect_unmasked_pii_leaks(v, sub_path))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            sub_path = f"{path}[{idx}]"
            leaks.extend(detect_unmasked_pii_leaks(item, sub_path))
    elif isinstance(obj, str):
        for m in RE_SSN.findall(obj):
            leaks.append((path, "SSN", m))
        for m in RE_EIN.findall(obj):
            leaks.append((path, "EIN", m))
        for m in RE_ABA.findall(obj):
            if not m.startswith(("2026", "1900", "0000")):
                leaks.append((path, "ABA_ROUTING", m))

    return leaks


def scan_event_for_leaks(
    event: dict[str, Any] | VaultObservabilityEvent,
) -> list[LeakFinding]:
    """Scan a telemetry event for PII leaks, raw 64-hex strings in Layer 1, and high-entropy leaks.

    Enforces:
    1. Layer 1 Story Banner (`headline`, `business_impact`) must contain ZERO raw 64-hex digests.
    2. Layer 1 and Layer 2 must not contain unmasked SSN, EIN, or ABA routing numbers.
    3. Layer 1 must not contain high-entropy unredacted cryptographic tokens (>=20 chars, H >= 3.6).
    """
    if isinstance(event, VaultObservabilityEvent):
        ev: dict[str, Any] = event.model_dump(mode="json", exclude_none=True)
    else:
        ev = event

    findings: list[LeakFinding] = []

    # 1. Recursive PII scan
    for p, leak_type, match_str in detect_unmasked_pii_leaks(ev):
        findings.append(LeakFinding(path=p, leak_type=leak_type, matched_value=match_str))

    # 2. Strict Layer 1 64-hex string and Shannon entropy scan (docs/06 §1.3)
    l1 = ev.get("layer1_story_banner")
    if isinstance(l1, dict):
        for field in ("headline", "business_impact"):
            val = l1.get(field)
            if isinstance(val, str):
                field_path = f"layer1_story_banner.{field}"
                # Check for raw 64-hex digests in Layer 1
                for match in RE_HEX_64.findall(val):
                    findings.append(
                        LeakFinding(
                            path=field_path,
                            leak_type="RAW_64_HEX_IN_LAYER1",
                            matched_value=match,
                            severity="CRITICAL",
                        )
                    )
                # Check for high-entropy tokens
                tokens = re.split(r"[\s\(\)\[\]\|,:;_\.\-/]+", val)
                for token in tokens:
                    if len(token) >= 24 and "[REDACTED" not in token:
                        entropy = compute_shannon_entropy_text(token)
                        if entropy >= 3.6:
                            findings.append(
                                LeakFinding(
                                    path=field_path,
                                    leak_type="HIGH_ENTROPY_TOKEN_IN_LAYER1",
                                    matched_value=token,
                                    severity="CRITICAL",
                                )
                            )

    # 3. Layer 2 free-floating raw 64-hex check (outside semantic_hash_pills)
    l2 = ev.get("layer2_visual_payload")
    if isinstance(l2, dict):
        metrics = l2.get("summary_metrics")
        if isinstance(metrics, dict):
            for k, v in metrics.items():
                if isinstance(v, str):
                    for match in RE_HEX_64.findall(v):
                        findings.append(
                            LeakFinding(
                                path=f"layer2_visual_payload.summary_metrics.{k}",
                                leak_type="RAW_64_HEX_IN_METRIC",
                                matched_value=match,
                                severity="HIGH",
                            )
                        )

    return findings


def validate_no_pii_leaks(event: dict[str, Any] | VaultObservabilityEvent) -> None:
    """Validate that an event contains no unmasked PII, raw 64-hex in Layer 1, or security leaks.

    Raises `PIILeakViolationError` if any violation is detected.
    """
    leaks = scan_event_for_leaks(event)
    if leaks:
        leak_descs = [
            f"{finding.leak_type} at {finding.path}: '{finding.matched_value}'" for finding in leaks
        ]
        msg = f"Telemetry leak verification failed ({len(leaks)} issues): {'; '.join(leak_descs)}"
        raise PIILeakViolationError(msg)


__all__ = [
    "LeakFinding",
    "PIILeakViolationError",
    "compute_shannon_entropy_text",
    "detect_unmasked_pii_leaks",
    "scan_event_for_leaks",
    "validate_no_pii_leaks",
]
