"""FIBO reference ontology grounding verifier (Task 5A.1 / Issue #211).

Binds GroundingVerifier SPI protocol from opendv_contracts.spi.grounding.
Asserts cryptographic integrity of packaged FIBO core pack against baseline digest.
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from typing import Any, Final

from opendv_contracts.dtos.matching import ReferenceConcept
from opendv_contracts.dtos.ontology import ALLOWED_PATH_A_PREDICATES
from opendv_contracts.spi.grounding import GroundingVerificationResult, GroundingVerifier
from opendv_service.ontology.matching.bm25 import (
    GROUNDING_THRESHOLD as BM25_GROUNDING_THRESHOLD,
)
from opendv_service.ontology.matching.bm25 import (
    Bm25ConceptMatcher,
    ConceptMatcher,
)
from opendv_sources.fibo.loader import (
    CANONICAL_FIBO_CORE_SHA256,
    FiboReferenceOntologyLoader,
    ReferenceOntologyCorruptError,
    get_default_fibo_loader,
)

GROUNDING_THRESHOLD: Final[float] = float(BM25_GROUNDING_THRESHOLD)

# Fail-closed allow-list of authorized Path A grounding predicates (docs/19 §4.3)
ALLOWED_GROUNDING_PREDICATES: Final[frozenset[str]] = frozenset(
    {p.lower() for p in ALLOWED_PATH_A_PREDICATES}
    | {
        "exactmatch",
        "closematch",
        "https://www.w3.org/2004/02/skos/core#exactmatch",
        "https://www.w3.org/2004/02/skos/core#closematch",
    }
)


def is_allowed_path_a_predicate(predicate: str | None) -> bool:
    """Return True iff predicate is an authorized Path A grounding predicate."""
    if not predicate or not isinstance(predicate, str):
        return False
    return predicate.strip().lower() in ALLOWED_GROUNDING_PREDICATES


class FiboPackGroundingVerifier(GroundingVerifier):
    """GroundingVerifier implementation backed by authoritative EDM Council FIBO core pack."""

    def __init__(
        self,
        loader: FiboReferenceOntologyLoader | None = None,
        matcher: ConceptMatcher | None = None,
    ) -> None:
        self._loader = loader or get_default_fibo_loader()

        # Enforce cryptographic baseline digest verification (docs/21 Task 5A.1)
        actual_sha256 = self._loader.pack_sha256
        if actual_sha256 != CANONICAL_FIBO_CORE_SHA256:
            msg = (
                f"FIBO reference pack SHA-256 '{actual_sha256}' mismatches "
                f"canonical baseline digest '{CANONICAL_FIBO_CORE_SHA256}'"
            )
            raise ReferenceOntologyCorruptError(
                msg,
                expected_sha256=CANONICAL_FIBO_CORE_SHA256,
                computed_sha256=actual_sha256,
            )

        if matcher is not None:
            self._matcher = matcher
        else:
            self._matcher = self._build_default_matcher()

    def _build_default_matcher(self) -> Bm25ConceptMatcher:
        """Extract classes from FIBO loader and index into Bm25ConceptMatcher."""
        concepts: list[ReferenceConcept] = []
        classes_map = self._loader.get_classes()
        for uri, data in classes_map.items():
            raw_label = data.get("label")
            if not raw_label or not isinstance(raw_label, str):
                raw_label = uri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
            name = str(raw_label)
            mod = str(data.get("module") or "UNKNOWN")
            definition = str(data.get("definition") or "")

            raw_syn = data.get("synonyms")
            if isinstance(raw_syn, str):
                synonyms = [raw_syn]
            elif isinstance(raw_syn, list):
                synonyms = [str(s) for s in raw_syn]
            else:
                synonyms = []

            raw_parents = data.get("sub_class_of")
            if isinstance(raw_parents, str):
                parents = [raw_parents]
            elif isinstance(raw_parents, list):
                parents = [str(p) for p in raw_parents]
            else:
                parents = []
            concepts.append(
                ReferenceConcept(
                    concept_uri=uri,
                    concept_name=name,
                    module=mod,
                    definition=definition,
                    synonyms=synonyms,
                    parent_concept_uris=parents,
                )
            )
        matcher = Bm25ConceptMatcher()
        matcher.index_concepts(concepts)
        return matcher

    @property
    def matcher(self) -> ConceptMatcher:
        """The underlying concept matcher."""
        return self._matcher

    @property
    def loader(self) -> FiboReferenceOntologyLoader:
        """The underlying FIBO reference loader."""
        return self._loader

    def verify_grounding(
        self,
        concept_uri: str,
        candidates: list[Any],
        concept_name: str = "",
        synonyms: Sequence[str] = (),
        **kwargs: Any,
    ) -> GroundingVerificationResult:
        """Verify whether concept_uri satisfies grounding threshold against reference candidates."""
        best_score = 0.0
        best_uri: str | None = None
        best_label: str | None = None
        diagnostics: list[str] = []
        threshold = float(kwargs.get("threshold", GROUNDING_THRESHOLD))

        clean_name = str(concept_name or "").strip()
        clean_synonyms = [str(s).strip() for s in (synonyms or ()) if str(s).strip()]

        queries: list[str] = []
        if clean_name:
            queries.append(clean_name)
        for syn in clean_synonyms:
            if syn not in queries:
                queries.append(syn)
        if clean_name and clean_synonyms:
            combined = " ".join([clean_name, *clean_synonyms])
            if combined not in queries:
                queries.append(combined)

        if not queries:
            explicit_query = str(kwargs.get("query") or "").strip()
            if explicit_query:
                queries.append(explicit_query)
            else:
                raw_token = concept_uri.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
                fallback_q = raw_token.replace("_", " ").strip()
                if fallback_q:
                    queries.append(fallback_q)

        # Evaluate explicit Path A external mappings only (fail-closed when none qualify)
        for cand in candidates or []:
            target_uri = (
                getattr(cand, "target_uri", None)
                or getattr(cand, "standard_uri", None)
                or (cand.get("target_uri") if isinstance(cand, dict) else None)
                or (cand.get("standard_uri") if isinstance(cand, dict) else None)
            )
            raw_pred = (
                getattr(cand, "predicate", None)
                if hasattr(cand, "predicate")
                else (cand.get("predicate") if isinstance(cand, dict) else None)
            )
            if raw_pred is None:
                raw_pred = (
                    getattr(cand, "mapping_type", None)
                    if hasattr(cand, "mapping_type")
                    else (cand.get("mapping_type") if isinstance(cand, dict) else None)
                )

            # Fail-closed predicate allow-list for Path A grounding (docs/19 §4.3)
            if not is_allowed_path_a_predicate(str(raw_pred) if raw_pred is not None else None):
                diagnostics.append(
                    f"Candidate {target_uri} with predicate '{raw_pred}' is not an "
                    "authorized Path A grounding predicate (must be skos:exactMatch "
                    "or skos:closeMatch)."
                )
                continue

            if not target_uri:
                continue

            # Verify target exists in reference pack or indexed matcher
            ref_entity = self._loader.lookup_by_uri(target_uri)
            ref_concept = self._matcher.lookup_concept(target_uri)
            if ref_entity is None and ref_concept is None:
                diagnostics.append(f"Target URI '{target_uri}' not found in FIBO reference pack.")
                continue

            # Compute verified BM25 score against reference concept across label + synonyms
            computed_score = 0.0
            for q in queries:
                scored = self._matcher.score(q, target_uri)
                if scored is not None and scored.normalized_score > computed_score:
                    computed_score = scored.normalized_score

            cand_score: float | None = None
            raw_conf = (
                getattr(cand, "mapping_confidence", None)
                if hasattr(cand, "mapping_confidence")
                else (cand.get("mapping_confidence") if isinstance(cand, dict) else None)
            )
            if raw_conf is None and not hasattr(cand, "mapping_confidence"):
                raw_conf = (
                    getattr(cand, "confidence", None)
                    if hasattr(cand, "confidence")
                    else (cand.get("confidence") if isinstance(cand, dict) else None)
                )
            if raw_conf is not None:
                try:
                    cand_score = float(raw_conf)
                except (ValueError, TypeError):
                    cand_score = 0.0

            if cand_score is not None:
                score_flt = min(cand_score, computed_score)
            else:
                score_flt = computed_score

            if score_flt > best_score or best_uri is None:
                best_score = score_flt
                best_uri = target_uri
                best_label = (
                    str(ref_entity.get("label", ""))
                    if ref_entity is not None
                    else (ref_concept.concept_name if ref_concept is not None else "")
                )

        if not candidates:
            suggestion_note = ""
            if queries:
                matches = self._matcher.match(queries[0], top_k=1)
                if matches:
                    top_m = matches[0]
                    suggestion_note = (
                        f" Top lexical suggestion (not grounded without committed mapping): "
                        f"'{top_m.concept_uri}' (score={top_m.normalized_score:.4f})."
                    )
            diagnostics.append(
                f"Concept '{concept_uri}' has zero Path A external mappings.{suggestion_note}"
            )

        is_grounded = best_uri is not None and best_score >= threshold
        if not is_grounded:
            diagnostics.append(
                f"Concept '{concept_uri}' grounding score {best_score:.4f} is below "
                f"threshold {threshold:.4f}."
            )
            if best_score < threshold and not candidates:
                best_uri = None
                best_label = None

        return GroundingVerificationResult(
            is_grounded=is_grounded,
            score=best_score,
            matched_uri=best_uri if is_grounded or best_score > 0.0 else None,
            matched_label=best_label if is_grounded or best_score > 0.0 else None,
            diagnostics=diagnostics,
        )


@functools.lru_cache(maxsize=1)
def get_default_grounding_verifier() -> FiboPackGroundingVerifier:
    """Return cached default FiboPackGroundingVerifier backed by vendored FIBO core pack."""
    return FiboPackGroundingVerifier()
