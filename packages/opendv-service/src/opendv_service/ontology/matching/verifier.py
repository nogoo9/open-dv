"""FIBO reference ontology grounding verifier (Task 5A.1 / Issue #211).

Binds GroundingVerifier SPI protocol from opendv_contracts.spi.grounding.
Asserts cryptographic integrity of packaged FIBO core pack against baseline digest.
"""

from __future__ import annotations

from typing import Any, Final

from opendv_contracts.dtos.matching import ReferenceConcept
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
ALLOWED_GROUNDING_PREDICATES: Final[set[str]] = {
    "skos:exactmatch",
    "http://www.w3.org/2004/02/skos/core#exactmatch",
    "https://www.w3.org/2004/02/skos/core#exactmatch",
    "owl:equivalentclass",
    "http://www.w3.org/2002/07/owl#equivalentclass",
    "https://www.w3.org/2002/07/owl#equivalentclass",
    "skos:closematch",
    "http://www.w3.org/2004/02/skos/core#closematch",
    "https://www.w3.org/2004/02/skos/core#closematch",
}


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
        **kwargs: Any,
    ) -> GroundingVerificationResult:
        """Verify whether concept_uri satisfies grounding threshold against reference candidates."""
        best_score = 0.0
        best_uri: str | None = None
        best_label: str | None = None
        diagnostics: list[str] = []

        raw_token = concept_uri.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        query = kwargs.get("concept_name") or kwargs.get("query") or raw_token.replace("_", " ")

        # 1. Evaluate explicit external mappings
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
            predicate = str(raw_pred).strip().lower() if raw_pred is not None else ""

            # Fail-closed predicate allow-list for Path A grounding (docs/19 §4.3)
            if predicate not in ALLOWED_GROUNDING_PREDICATES:
                diagnostics.append(
                    f"Candidate {target_uri} with predicate '{raw_pred}' is not an "
                    "authorized Path A grounding predicate (must be exactMatch, "
                    "equivalentClass, or closeMatch)."
                )
                continue

            if not target_uri:
                continue

            # Verify target exists in reference pack
            ref_entity = self._loader.lookup_by_uri(target_uri)
            if ref_entity is None:
                diagnostics.append(f"Target URI '{target_uri}' not found in FIBO reference pack.")
                continue

            # Compute verified BM25 score against reference concept
            computed_score = 0.0
            if query:
                scored = self._matcher.score(query, target_uri)
                if scored is not None:
                    computed_score = scored.normalized_score

            cand_score: float | None = None
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

            if score_flt > best_score:
                best_score = score_flt
                best_uri = target_uri
                best_label = str(ref_entity.get("label", ""))

        # 2. Free-text index matching fallback ONLY if no explicit candidates were supplied
        if not candidates and query:
            matches = self._matcher.match(query, top_k=1)
            if matches:
                top_m = matches[0]
                best_score = top_m.normalized_score
                best_uri = top_m.concept_uri
                best_label = top_m.concept_name

        is_grounded = best_score >= GROUNDING_THRESHOLD
        if not is_grounded:
            diagnostics.append(
                f"Concept '{concept_uri}' grounding score {best_score:.4f} is below "
                f"threshold {GROUNDING_THRESHOLD:.4f}."
            )

        return GroundingVerificationResult(
            is_grounded=is_grounded,
            score=best_score,
            matched_uri=best_uri,
            matched_label=best_label,
            diagnostics=diagnostics,
        )
