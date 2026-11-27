"""Synchronous Layer-2a KnowledgeSource SPI protocol for ontology grounding."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from decimal import Decimal
from typing import Protocol, runtime_checkable

from opendv.models.grounding import ColumnEvidence, InferredValueProfile
from opendv.models.grounding_spi import (
    BackendReasoningResult,
    CalibrationParams,
    KnowledgeItem,
    KnowledgeSourceMetadata,
    RecallHit,
    TableReasoningContext,
)


@runtime_checkable
class KnowledgeSource(Protocol):
    """Modality-neutral synchronous knowledge-source protocol (Module 2 SPI)."""

    @property
    def metadata(self) -> KnowledgeSourceMetadata:
        """Return static source identity, digest, and anchor capabilities."""
        ...

    @property
    def calibration_params(self) -> CalibrationParams | None:
        """Return calibrated parameters if available, else None to use analytic defaults."""
        ...

    def expand_query_terms(
        self,
        tokens: Sequence[str],
        *,
        context_text: str | None = None,
    ) -> list[str]:
        """Expand domain-specific acronyms or abbreviations in context."""
        ...

    def recall_by_text(
        self,
        query: str,
        *,
        entry_kinds: AbstractSet[str] | None = None,
        top_k: int = 25,
    ) -> list[RecallHit]:
        """Recall top-k candidate entries matching normalized lexical query text."""
        ...

    def match_values_or_mentions(
        self,
        surface_forms: Sequence[str],
        *,
        pii_tags: Sequence[str] = (),
        context_hint: str | None = None,
        structural_patterns: Mapping[str, float] | None = None,
    ) -> list[RecallHit]:
        """Match observed surface codes, lifted individuals, or structural value patterns."""
        ...

    def get_item(self, entry_id: str) -> KnowledgeItem | None:
        """Fetch full normalized KnowledgeItem for entry_id, or None if absent."""
        ...

    def get_ancestors(self, entry_id: str, *, max_depth: int = 8) -> list[str]:
        """Return taxonomic ancestor entry_ids ordered nearest-first up to max_depth."""
        ...

    def evaluate_structured_candidates(
        self,
        column: ColumnEvidence,
        profile: InferredValueProfile,
        candidates: Sequence[RecallHit],
        *,
        table_context: TableReasoningContext | None = None,
    ) -> list[BackendReasoningResult]:
        """Evaluate shape/range/inverse/disjointness constraints for candidate hits."""
        ...

    def score(self, query: str, entry_id: str) -> Decimal:
        """Compute normalized lexical match score in [0, 1] for query vs entry_id."""
        ...


__all__ = ["KnowledgeSource"]
