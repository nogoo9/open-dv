"""Privacy, confidential taint tracking, and HMAC-SHA256 pseudonymization."""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from typing import Any, Final

from pydantic import BaseModel

from opendv_contracts.dtos.ingestion import EvidenceChunk, IngestionResult, TableStructure
from opendv_contracts.errors import ErrorCode, OpenDVError

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
        """Derive or retrieve a 16-hex HMAC-SHA256 pseudonym for `term`."""
        if not term:
            return term
        if term in self._token_to_pseudo:
            return self._token_to_pseudo[term]

        token_hash = hmac.new(self._k_pseudo, term.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
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
            pattern = re.compile(
                rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])",
                re.IGNORECASE,
            )
            result = pattern.sub(lambda _m, p=pseudo: p, result)
        return result

    def _mask_metadata_value(self, value: Any, effective_terms: Sequence[str], key: str) -> Any:
        """Recursively pseudonymize string values inside chunk metadata."""
        if isinstance(value, str):
            masked_v = self.pseudonymize_text(value, effective_terms)
            if any(t.strip() and t.strip().lower() in masked_v.lower() for t in effective_terms):
                raise OpenDVError(
                    f"Residual confidential substring survived in chunk metadata key '{key}'.",
                    error_code=ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN,
                )
            return masked_v
        if isinstance(value, Mapping):
            return {
                k: self._mask_metadata_value(v, effective_terms, f"{key}.{k}")
                for k, v in value.items()
                if k != "sensitive_entities"
            }
        if isinstance(value, list):
            return [self._mask_metadata_value(elem, effective_terms, key) for elem in value]
        if isinstance(value, tuple):
            return tuple(self._mask_metadata_value(elem, effective_terms, key) for elem in value)
        return value

    def pseudonymize_chunk(
        self,
        chunk: EvidenceChunk,
        sensitive_terms: Sequence[str] | None = None,
    ) -> EvidenceChunk:
        """Return a copy of `chunk` with sensitive terms replaced by HMAC pseudonyms."""
        raw_meta_terms = (
            chunk.metadata.get("sensitive_entities")
            if chunk.metadata and isinstance(chunk.metadata, dict)
            else None
        )
        effective_terms: list[str] = [
            str(t).strip() for t in (sensitive_terms or []) if str(t).strip()
        ]
        if not effective_terms and isinstance(raw_meta_terms, (list, tuple)):
            effective_terms = [str(t).strip() for t in raw_meta_terms if str(t).strip()]
        if not effective_terms:
            if chunk.metadata and "sensitive_entities" in chunk.metadata:
                stripped_meta = dict(chunk.metadata)
                stripped_meta.pop("sensitive_entities", None)
                return chunk.model_copy(update={"metadata": stripped_meta})
            return chunk

        new_text = self.pseudonymize_text(chunk.text, effective_terms)
        lower_new = new_text.lower()
        for raw_term in effective_terms:
            clean_term = raw_term.strip()
            if clean_term and clean_term.lower() in lower_new:
                raise OpenDVError(
                    f"Residual confidential substring '{clean_term}' survived pseudonymization.",
                    error_code=ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN,
                )

        new_meta: dict[str, Any] = dict(chunk.metadata) if chunk.metadata else {}
        new_meta.pop("sensitive_entities", None)
        for k, v in list(new_meta.items()):
            new_meta[k] = self._mask_metadata_value(v, effective_terms, str(k))
        new_meta["redacted_entity_count"] = len(effective_terms)
        new_meta["pseudonymized"] = True
        return chunk.model_copy(update={"text": new_text, "metadata": new_meta})

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


def _is_public_taint(taint: Any) -> bool:
    if taint is None:
        return False
    val = taint.value if hasattr(taint, "value") else str(taint)
    return str(val).lower().strip() == "public"


def _walk_privacy_input(val: Any, visited: set[int]) -> None:
    """Recursively inspect TaskSpec input_data for placeholder leaks and taint violations."""
    if val is None or isinstance(val, (int, float, bool, bytes, bytearray)):
        return
    if isinstance(val, str):
        scan_redacted_placeholder_leak(val)
        return

    obj_id = id(val)
    if obj_id in visited:
        return
    visited.add(obj_id)

    if isinstance(val, EvidenceChunk):
        scan_redacted_placeholder_leak(val.text)
        meta = val.metadata if isinstance(val.metadata, dict) else {}
        _walk_privacy_input(meta, visited)
        if meta.get("do_not_send_to_agent") is True:
            raise OpenDVError(
                f"EvidenceChunk '{val.chunk_id}' is marked do_not_send_to_agent and "
                "cannot be dispatched to an agent TaskSpec.",
                error_code=ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN,
            )
        if not _is_public_taint(val.taint):
            if meta.get("pseudonymized") is not True:
                raise OpenDVError(
                    f"Tainted EvidenceChunk '{val.chunk_id}' cannot be dispatched to an "
                    "agent TaskSpec without HMAC pseudonymization.",
                    error_code=ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN,
                )
            raw_sens = meta.get("sensitive_entities")
            sens = (
                [str(s).strip() for s in raw_sens if str(s).strip()]
                if isinstance(raw_sens, (list, tuple))
                else []
            )
            lower_txt = val.text.lower()
            for s_term in sens:
                if s_term.lower() in lower_txt:
                    raise OpenDVError(
                        f"Unredacted confidential entity '{s_term}' detected in tainted "
                        f"EvidenceChunk '{val.chunk_id}'.",
                        error_code=ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN,
                    )
        return

    if isinstance(val, TableStructure) or (
        hasattr(val, "table_id") and hasattr(val, "headers") and hasattr(val, "rows")
    ):
        tbl_id = str(getattr(val, "table_id", "") or "")
        caption = str(getattr(val, "caption", "") or "")
        raw_markup = str(getattr(val, "raw_html_or_md", "") or "")
        headers = [str(h) for h in (getattr(val, "headers", None) or [])]
        rows = getattr(val, "rows", None) or []
        cell_texts = [
            str(cell)
            for row in rows
            if isinstance(row, (list, tuple))
            for cell in row
            if cell is not None
        ]
        combined = "\n".join([tbl_id, caption, raw_markup, *headers, *cell_texts])
        scan_redacted_placeholder_leak(combined)
        meta = val.metadata if isinstance(getattr(val, "metadata", None), dict) else {}
        _walk_privacy_input(meta, visited)
        if meta.get("do_not_send_to_agent") is True:
            raise OpenDVError(
                f"TableStructure '{tbl_id}' is marked do_not_send_to_agent and "
                "cannot be dispatched to an agent TaskSpec.",
                error_code=ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN,
            )
        tbl_taint = (
            getattr(val, "taint", None)
            or getattr(val, "taint_classification", None)
            or meta.get("taint")
        )
        if not _is_public_taint(tbl_taint):
            if meta.get("pseudonymized") is not True:
                raise OpenDVError(
                    f"Tainted TableStructure '{tbl_id}' cannot be dispatched to an "
                    "agent TaskSpec without HMAC pseudonymization.",
                    error_code=ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN,
                )
            raw_sens = meta.get("sensitive_entities")
            sens = (
                [str(s).strip() for s in raw_sens if str(s).strip()]
                if isinstance(raw_sens, (list, tuple))
                else []
            )
            lower_comb = combined.lower()
            for s_term in sens:
                if s_term.lower() in lower_comb:
                    raise OpenDVError(
                        f"Unredacted confidential entity '{s_term}' detected in tainted "
                        f"TableStructure '{tbl_id}'.",
                        error_code=ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN,
                    )
        return

    if isinstance(val, BaseModel):
        model_meta = getattr(val, "metadata", None)
        if isinstance(model_meta, Mapping) and model_meta.get("do_not_send_to_agent") is True:
            raise OpenDVError(
                f"{type(val).__name__} is marked do_not_send_to_agent and "
                "cannot be dispatched to an agent TaskSpec.",
                error_code=ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN,
            )
        if (
            isinstance(val, IngestionResult)
            and not _is_public_taint(val.taint)
            and val.normalized_text
            and not (isinstance(model_meta, Mapping) and model_meta.get("pseudonymized") is True)
        ):
            raise OpenDVError(
                f"Tainted IngestionResult '{val.document_id}' with raw normalized_text "
                "cannot be dispatched to an agent TaskSpec without HMAC pseudonymization.",
                error_code=ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN,
            )
        for field_name in type(val).model_fields:
            _walk_privacy_input(getattr(val, field_name, None), visited)
        extra = getattr(val, "model_extra", None)
        if isinstance(extra, Mapping):
            _walk_privacy_input(extra, visited)
        return

    if isinstance(val, Mapping):
        for k, v in val.items():
            _walk_privacy_input(k, visited)
            _walk_privacy_input(v, visited)
        return

    if isinstance(val, (Sequence, AbstractSet)):
        for elem in val:
            _walk_privacy_input(elem, visited)
        return


def validate_task_spec_privacy(
    prompt: str,
    system_instructions: str = "",
    input_data: Any = None,
) -> None:
    """Enforce redacted placeholder and confidential taint guards on TaskSpec builders."""
    scan_redacted_placeholder_leak(prompt)
    if system_instructions:
        scan_redacted_placeholder_leak(system_instructions)

    if input_data is None:
        return
    _walk_privacy_input(input_data, set())
