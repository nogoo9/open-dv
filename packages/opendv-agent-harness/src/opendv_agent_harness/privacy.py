"""Privacy, confidential taint tracking, and HMAC-SHA256 pseudonymization."""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Sequence
from typing import Final

from opendv_contracts.dtos.ingestion import EvidenceChunk

REDACTED_PLACEHOLDER_PATTERN: Final[re.Pattern[str]] = re.compile(r"\[REDACTED_ODV_[A-Z0-9_]+\]")
PSEUDONYM_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?i)(?:(?<=[\W_])|^)pseudo[_\s][a-f0-9]{12,64}(?![a-f0-9])"
)


class RedactedPlaceholderLeakError(ValueError):
    """Raised when [REDACTED_ODV_*] placeholder token leaks into an entity or prompt."""

    exit_code: int = 4
    http_status: int = 422
    error_code: str = "REDACTED_PLACEHOLDER_LEAK"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AgentUnresolvedPseudonymError(ValueError):
    """Raised when unresolved HMAC pseudonym token remains in agent output."""

    exit_code: int = 4
    http_status: int = 422
    error_code: str = "AGENT_UNRESOLVED_PSEUDONYM"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AgentDestructiveOpForbiddenError(Exception):
    """Raised when automated agent attempts to emit destructive delta operations."""

    exit_code: int = 7
    http_status: int = 403
    error_code: str = "AGENT_DESTRUCTIVE_OP_FORBIDDEN"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DanglingParentReferenceError(ValueError):
    """Raised when an explicit child delta operation targets a non-existent parent
    concept or enum.
    """

    exit_code: int = 4
    http_status: int = 422
    error_code: str = "DANGLING_PARENT_REFERENCE"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def scan_redacted_placeholder_leak(text: str) -> None:
    """Scan text for prohibited redacted placeholder tokens ([REDACTED_ODV_*]).

    Raises:
        RedactedPlaceholderLeakError: If any placeholder token is found.
    """
    match = REDACTED_PLACEHOLDER_PATTERN.search(text)
    if match:
        raise RedactedPlaceholderLeakError(
            f"Redacted placeholder token '{match.group(0)}' leaked into output text."
        )


def scan_unresolved_pseudonyms(text: str) -> None:
    """Scan text for unresolved HMAC pseudonym tokens (pseudo_*).

    Raises:
        AgentUnresolvedPseudonymError: If any unmapped pseudonym token is found.
    """
    match = PSEUDONYM_TOKEN_PATTERN.search(text)
    if match:
        raise AgentUnresolvedPseudonymError(
            f"Unresolved HMAC pseudonym token '{match.group(0)}' detected in agent output."
        )


class Pseudonymizer:
    """Deterministic HMAC-SHA256 pseudonymizer using injected K_pseudo."""

    def __init__(self, k_pseudo: bytes) -> None:
        if not isinstance(k_pseudo, bytes) or len(k_pseudo) == 0:
            raise ValueError("k_pseudo must be non-empty bytes")
        self._k_pseudo: bytes = k_pseudo
        self._token_to_pseudo: dict[str, str] = {}
        self._pseudo_to_token: dict[str, str] = {}
        self._cached_de_regex: re.Pattern[str] | None = None
        self._cached_lookup: dict[str, str] | None = None

    def __repr__(self) -> str:
        return f"Pseudonymizer(active_tokens={len(self._token_to_pseudo)})"

    def pseudonymize_token(self, term: str) -> str:
        """Derive or retrieve a 12-hex HMAC-SHA256 pseudonym for `term`."""
        if not term:
            return term
        if term in self._token_to_pseudo:
            return self._token_to_pseudo[term]

        token_hash = hmac.new(self._k_pseudo, term.encode("utf-8"), hashlib.sha256).hexdigest()[:12]
        pseudo = f"pseudo_{token_hash}"
        if pseudo in self._pseudo_to_token and self._pseudo_to_token[pseudo] != term:
            raise ValueError(
                f"Pseudonym collision detected for token '{pseudo}': "
                f"already mapped to '{self._pseudo_to_token[pseudo]}', cannot map to '{term}'"
            )
        self._token_to_pseudo[term] = pseudo
        self._pseudo_to_token[pseudo] = term
        self._cached_de_regex = None
        self._cached_lookup = None
        return pseudo

    def pseudonymize_text(self, text: str, sensitive_terms: Sequence[str]) -> str:
        """Replace occurrences of sensitive terms with their HMAC pseudonyms."""
        if not text or not sensitive_terms:
            return text

        # Sort longest terms first to prevent partial token shadowing; secondary sort by
        # term string ensures deterministic substitution order across runs (MIN-06)
        sorted_terms = sorted(
            {t.strip() for t in sensitive_terms if t.strip()},
            key=lambda t: (-len(t), t),
        )
        result = text
        for term in sorted_terms:
            pseudo = self.pseudonymize_token(term)
            pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
            result = pattern.sub(lambda _m, p=pseudo: p, result)
        return result

    def pseudonymize_chunk(
        self,
        chunk: EvidenceChunk,
        sensitive_terms: Sequence[str] | None = None,
    ) -> EvidenceChunk:
        """Return a copy of `chunk` with sensitive terms replaced by HMAC pseudonyms."""
        terms = sensitive_terms or []
        if not terms:
            return chunk
        new_text = self.pseudonymize_text(chunk.text, terms)
        return chunk.model_copy(update={"text": new_text})

    def de_pseudonymize(self, text: str) -> str:
        """Map all known `pseudo_*` tokens back to their original plain text.

        Uses case-insensitive single-pass alternation regex lookup and a callable
        replacement to prevent backslash escape corruption or bad-escape re.error (MAJ-01, SUG-02).
        """
        if not text or not self._pseudo_to_token:
            return text

        if self._cached_de_regex is None or self._cached_lookup is None:
            self._cached_lookup = {k.lower(): v for k, v in self._pseudo_to_token.items()}
            sorted_keys = sorted(self._pseudo_to_token.keys(), key=len, reverse=True)
            # Trailing negative lookahead (?![a-f0-9]) prevents partial token replacement
            # in longer hex strings (SUG-01)
            self._cached_de_regex = re.compile(
                rf"(?:{'|'.join(re.escape(k) for k in sorted_keys)})(?![a-f0-9])",
                re.IGNORECASE,
            )
        lookup = self._cached_lookup
        return self._cached_de_regex.sub(lambda m: lookup.get(m.group(0).lower(), m.group(0)), text)

    def scan_for_leaks(self, text: str) -> None:
        """Verify text contains no raw placeholders and no unresolved pseudonyms."""
        scan_redacted_placeholder_leak(text)
        scan_unresolved_pseudonyms(text)
