"""Telemetry PII, raw digest, and Layer 1 high-entropy token scrubbing helpers."""

from __future__ import annotations

import copy
import re
from typing import Any, Final

from opendv_telemetry import compute_shannon_entropy_text

_RE_SSN: Final[re.Pattern[str]] = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_RE_EIN: Final[re.Pattern[str]] = re.compile(r"\b\d{2}-\d{7}\b")
_RE_ABA: Final[re.Pattern[str]] = re.compile(r"\b\d{9}\b")
_RE_EMAIL: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RE_HEX_RUN: Final[re.Pattern[str]] = re.compile(
    r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{24,}(?![0-9A-Fa-f])"
)
_RE_HIGH_ENTROPY: Final[re.Pattern[str]] = re.compile(r"[^\s\(\)\[\]\|,:;_\.\-/]{24,}")


def redact_pii_text(text: str, *, mask_hex64: bool = False) -> str:
    """Scrub SSN, EIN, ABA, email, and optionally 24+ hex / high-entropy tokens."""
    cleaned = _RE_SSN.sub("<MASKED_PII>", text)
    cleaned = _RE_EIN.sub("<MASKED_PII>", cleaned)
    cleaned = _RE_ABA.sub(
        lambda m: (
            m.group(0) if m.group(0).startswith(("2026", "1900", "0000")) else "<MASKED_PII>"
        ),
        cleaned,
    )
    cleaned = _RE_EMAIL.sub("<MASKED_PII>", cleaned)
    if mask_hex64:
        cleaned = _RE_HEX_RUN.sub("[REDACTED_DIGEST]", cleaned)
        cleaned = _RE_HIGH_ENTROPY.sub(
            lambda m: (
                "[REDACTED_DIGEST]"
                if "[REDACTED" not in m.group(0)
                and compute_shannon_entropy_text(m.group(0)) >= 3.6
                else m.group(0)
            ),
            cleaned,
        )
    return cleaned


def sanitize_layer1_summary(text: str, max_len: int = 100) -> str:
    """Redact PII/digests/high-entropy tokens before and after bounding Layer 1 summary text."""
    first_pass = redact_pii_text(text, mask_hex64=True)
    return redact_pii_text(first_pass[:max_len], mask_hex64=True)


def _redact_obj(obj: Any, *, mask_hex64: bool = False) -> Any:
    if isinstance(obj, str):
        return redact_pii_text(obj, mask_hex64=mask_hex64)
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k == "session_id":
                out[k] = v
                continue
            sub_mask = mask_hex64 or k in ("layer1_story_banner", "summary_metrics")
            out[k] = _redact_obj(v, mask_hex64=sub_mask)
        return out
    if isinstance(obj, list):
        return [_redact_obj(item, mask_hex64=mask_hex64) for item in obj]
    return obj


def redact_event_pii(event: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copied observability event with PII/raw-digest leaks scrubbed."""
    return _redact_obj(copy.deepcopy(event), mask_hex64=False)


__all__ = [
    "redact_event_pii",
    "redact_pii_text",
    "sanitize_layer1_summary",
]
