"""BM25 matcher configuration, schema loader, and concept/hit dataclasses."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from pydantic import Field

from opendv.models.diagnostics import StrictModel
from opendv.models.errors import ErrorCode, OpenDVError, ReferenceOntologyCorruptError
from opendv.sources.fibo.loader import (
    CANONICAL_FIBO_CORE_SHA256,
    FiboReferenceOntologyLoader,
)

_ZERO: Final[Decimal] = Decimal("0.0")


class Bm25WeightsConfig(StrictModel):
    """Per-field BM25 weights matching schemas/v2/bm25_fibo_v1.schema.json."""

    w_name: str
    w_syn: str
    w_def: str


class Bm25MatcherConfig(StrictModel):
    """Pinned BM25 matcher configuration loaded from schemas/v2/matchers/bm25_fibo_v1.json."""

    schema_url: str | None = Field(default=None, alias="$schema")
    matcher_id: str
    version: str
    k1: str
    b: str
    weights: Bm25WeightsConfig
    hit_gate_cap: str
    grounding_threshold: str


def _find_default_bm25_config_path() -> Path | None:
    current: Path | None = Path(__file__).resolve().parent
    for _ in range(8):
        if current is None:
            break
        candidate = current / "schemas" / "v2" / "matchers" / "bm25_fibo_v1.json"
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def load_bm25_matcher_config(config_path: Path | str | None = None) -> Bm25MatcherConfig:
    """Load and validate Bm25MatcherConfig from schemas/v2/matchers/bm25_fibo_v1.json."""
    resolved = Path(config_path) if config_path is not None else _find_default_bm25_config_path()
    if resolved is not None:
        if not resolved.is_file():
            raise OpenDVError(
                f"BM25 matcher config not found: {resolved}",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        try:
            raw = json.loads(resolved.read_text(encoding="utf-8"))
            return Bm25MatcherConfig.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise OpenDVError(
                f"Invalid BM25 matcher config at '{resolved}': {exc}",
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            ) from exc

    return Bm25MatcherConfig(
        matcher_id="bm25_fibo_v1",
        version="1.0.0",
        k1="1.5",
        b="0.75",
        weights=Bm25WeightsConfig(w_name="2.5", w_syn="2.0", w_def="1.0"),
        hit_gate_cap="0.6400",
        grounding_threshold="0.6500",
    )


@dataclass(frozen=True)
class Bm25ConceptInput:
    """Normalized reference concept record indexed by Bm25ConceptMatcher."""

    concept_uri: str
    concept_name: str
    module: str = ""
    definition: str = ""
    synonyms: tuple[str, ...] = ()
    parent_uri: str | None = None


@dataclass(frozen=True)
class Bm25ScoredHit:
    """Scored BM25 concept hit preserving exact quantized Decimal and float scores."""

    concept_uri: str
    concept_name: str
    module: str
    raw_score: float
    normalized_score: float
    matched_term: str
    definition: str
    parent_uri: str | None = None
    raw_decimal: Decimal = field(default=_ZERO)
    quantized_decimal: Decimal = field(default=_ZERO)


def coerce_concept(item: Bm25ConceptInput | Mapping[str, Any]) -> Bm25ConceptInput:
    """Coerce a concept dict or Bm25ConceptInput into Bm25ConceptInput."""
    if isinstance(item, Bm25ConceptInput):
        return item
    syns = item.get("synonyms", ())
    parent = item.get("parent_uri")
    if parent is None:
        sub_of = item.get("sub_class_of")
        if isinstance(sub_of, Sequence) and not isinstance(sub_of, str) and sub_of:
            parent = str(sub_of[0])
    return Bm25ConceptInput(
        concept_uri=str(item.get("concept_uri") or item.get("uri") or ""),
        concept_name=str(item.get("concept_name") or item.get("label") or ""),
        module=str(item.get("module") or ""),
        definition=str(item.get("definition") or ""),
        synonyms=tuple(str(s) for s in syns) if isinstance(syns, Sequence) else (),
        parent_uri=str(parent) if parent else None,
    )


def concepts_from_fibo_loader(loader: FiboReferenceOntologyLoader) -> list[Bm25ConceptInput]:
    """Verify canonical SHA-256 digest and convert FIBO loader classes to Bm25ConceptInput."""
    if loader.pack_sha256 != CANONICAL_FIBO_CORE_SHA256:
        raise ReferenceOntologyCorruptError(
            f"FIBO pack SHA-256 '{loader.pack_sha256}' does not match canonical "
            f"'{CANONICAL_FIBO_CORE_SHA256}'."
        )
    classes = loader.get_classes()
    concepts: list[Bm25ConceptInput] = []
    for uri in sorted(classes):
        entry = classes[uri]
        sub_of = entry.get("sub_class_of", [])
        parent_uri = str(sub_of[0]) if sub_of else None
        concepts.append(
            Bm25ConceptInput(
                concept_uri=uri,
                concept_name=str(entry.get("label", "")),
                module=str(entry.get("module", "")),
                definition=str(entry.get("definition", "")),
                synonyms=tuple(str(s) for s in entry.get("synonyms", [])),
                parent_uri=parent_uri,
            )
        )
    return concepts


__all__ = [
    "Bm25ConceptInput",
    "Bm25MatcherConfig",
    "Bm25ScoredHit",
    "Bm25WeightsConfig",
    "coerce_concept",
    "concepts_from_fibo_loader",
    "load_bm25_matcher_config",
]
