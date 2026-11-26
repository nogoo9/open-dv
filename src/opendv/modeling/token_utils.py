"""Token accounting and prompt compaction helpers using cl100k_base."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

import tiktoken

DEFAULT_CONTEXT_SOFT_CEILING_TOKENS = 40_000

_UNTRUSTED_OPEN_RE = re.compile(r"<\s*untrusted_business_context\s*>", re.IGNORECASE)
_UNTRUSTED_CLOSE_RE = re.compile(r"<\s*/\s*untrusted_business_context\s*>", re.IGNORECASE)


@lru_cache(maxsize=1)
def _cl100k_encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Count exact cl100k_base tokens in a text string."""
    if not text:
        return 0
    return len(_cl100k_encoding().encode(text))


def compact_json_dumps(payload: Any) -> str:
    """Serialize payload with minimal whitespace for token-lean LLM prompts."""
    return json.dumps(payload, separators=(",", ":"), default=str)


def format_untrusted_business_context(hints: str | None) -> str:
    """Wrap steward/caller business hints inside fenced untrusted delimiters."""
    if not hints or not hints.strip():
        return ""
    sanitized = _UNTRUSTED_OPEN_RE.sub("[untrusted_business_context]", hints.strip())
    sanitized = _UNTRUSTED_CLOSE_RE.sub("[/untrusted_business_context]", sanitized)
    return (
        "<untrusted_business_context>\n"
        f"{sanitized}\n"
        "</untrusted_business_context>"
    )


__all__ = [
    "DEFAULT_CONTEXT_SOFT_CEILING_TOKENS",
    "compact_json_dumps",
    "count_tokens",
    "format_untrusted_business_context",
]
