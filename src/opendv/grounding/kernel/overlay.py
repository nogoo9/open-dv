"""Multi-wave OverlayKnowledgeSource folding Wave-1 Graph A entries over a base source."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from decimal import Decimal
from typing import Final

from opendv.grounding.kernel._project_source import ProjectGraphASource, dedupe_str_list
from opendv.grounding.spi import KnowledgeSource
from opendv.models.grounding import (
    ColumnEvidence,
    InferredValueProfile,
    TableAnchorCandidate,
)
from opendv.models.grounding_spi import (
    BackendReasoningResult,
    CalibrationParams,
    KnowledgeItem,
    KnowledgeSourceMetadata,
    RecallHit,
    TableReasoningContext,
)
from opendv.models.ontology import GraphAOntology
from opendv.models.project import ConceptMemorySpec, DiscoveryMode

_PINNED_MIN_SCORE: Final[Decimal] = Decimal("0.9200")
_ONE_DEC: Final[Decimal] = Decimal("1.0000")
_ZERO_DEC: Final[Decimal] = Decimal("0.0000")


class OverlayKnowledgeSource:
    """Composite KnowledgeSource folding Wave-1 Graph A entries over a base source."""

    def __init__(
        self,
        base_source: KnowledgeSource,
        project_source: ProjectGraphASource | None = None,
        *,
        ontology: GraphAOntology | None = None,
        concept_memory: Sequence[ConceptMemorySpec] = (),
        steward_answers: Mapping[str, str] | Sequence[str] | None = None,
        discovery_mode: DiscoveryMode = "incremental_new",
    ) -> None:
        self.base_source = base_source
        self.project_source = project_source or ProjectGraphASource(
            ontology=ontology,
            concept_memory=concept_memory,
            steward_answers=steward_answers,
        )
        self.discovery_mode = discovery_mode
        self.is_bypassed: bool = discovery_mode == "full_rebuild"

    @property
    def metadata(self) -> KnowledgeSourceMetadata:
        """Return base source metadata combined with project anchor kinds when active."""
        base_meta = self.base_source.metadata
        if self.is_bypassed:
            return base_meta
        return KnowledgeSourceMetadata(
            source_id=base_meta.source_id,
            version=base_meta.version,
            sha256=base_meta.sha256,
            anchor_entry_kinds=(
                base_meta.anchor_entry_kinds | self.project_source.metadata.anchor_entry_kinds
            ),
            origin_kind=base_meta.origin_kind,
        )

    @property
    def calibration_params(self) -> CalibrationParams | None:
        """Delegate calibration_params to base_source."""
        return self.base_source.calibration_params

    @property
    def steward_notes(self) -> tuple[str, ...]:
        """Return normalized free-text steward answers from the project source."""
        return () if self.is_bypassed else self.project_source.steward_notes

    def expand_query_terms(
        self,
        tokens: Sequence[str],
        *,
        context_text: str | None = None,
    ) -> list[str]:
        """Combine query expansions from project_source and base_source."""
        if self.is_bypassed:
            return self.base_source.expand_query_terms(tokens, context_text=context_text)
        proj_exp = self.project_source.expand_query_terms(tokens, context_text=context_text)
        base_exp = self.base_source.expand_query_terms(tokens, context_text=context_text)
        return dedupe_str_list([*proj_exp, *base_exp])

    def _fold_and_merge_hits(
        self,
        proj_hits: Sequence[RecallHit],
        base_hits: Sequence[RecallHit],
        *,
        top_k: int = 25,
    ) -> list[RecallHit]:
        merged: dict[str, RecallHit] = {}
        std_to_proj = self.project_source.standard_to_project_uri

        for hit in base_hits:
            if self.project_source.is_muted(hit.entry_id, hit.label):
                continue
            folded_id = std_to_proj.get(hit.entry_id, hit.entry_id)
            if self.project_source.is_muted(folded_id, hit.label):
                continue
            if folded_id != hit.entry_id:
                proj_item = self.project_source.get_item(folded_id)
                folded = RecallHit(
                    entry_id=folded_id,
                    label=proj_item.label if proj_item else hit.label,
                    entry_kind=proj_item.entry_kind if proj_item else hit.entry_kind,
                    raw_score=hit.raw_score,
                    origin_source_id=self.project_source.metadata.source_id,
                    origin_kind="project_graph_a",
                )
            else:
                folded = hit
            if self.project_source.is_muted(folded.entry_id, folded.label):
                continue
            if folded.entry_id in self.project_source.pinned_reference_uris:
                folded = folded.model_copy(
                    update={
                        "origin_kind": "reference_standard",
                        "raw_score": min(_ONE_DEC, max(folded.raw_score, _PINNED_MIN_SCORE)),
                    }
                )
            elif folded.entry_id in self.project_source.pinned_project_terms:
                folded = folded.model_copy(
                    update={"raw_score": min(_ONE_DEC, max(folded.raw_score, _PINNED_MIN_SCORE))}
                )
            prev = merged.get(folded.entry_id)
            if prev is None or folded.raw_score > prev.raw_score:
                merged[folded.entry_id] = folded

        for hit in proj_hits:
            if self.project_source.is_muted(hit.entry_id, hit.label):
                continue
            adjusted = hit
            is_uncommitted_pin = hit.entry_id in self.project_source.pinned_reference_uris
            if is_uncommitted_pin:
                base_item = self.base_source.get_item(hit.entry_id)
                adjusted = hit.model_copy(
                    update={
                        "label": base_item.label if base_item else hit.label,
                        "entry_kind": base_item.entry_kind if base_item else hit.entry_kind,
                        "origin_kind": "reference_standard",
                    }
                )
            prev = merged.get(adjusted.entry_id)
            if prev is None:
                merged[adjusted.entry_id] = adjusted
            else:
                best_score = max(prev.raw_score, adjusted.raw_score)
                origin_kind = (
                    "reference_standard" if is_uncommitted_pin else adjusted.origin_kind
                )
                merged[adjusted.entry_id] = prev.model_copy(
                    update={"raw_score": best_score, "origin_kind": origin_kind}
                )

        ordered = sorted(merged.values(), key=lambda h: (-h.raw_score, h.entry_id))
        return ordered[:top_k]

    def recall_by_text(
        self,
        query: str,
        *,
        entry_kinds: AbstractSet[str] | None = None,
        top_k: int = 25,
    ) -> list[RecallHit]:
        """Recall merged and Wave-1-folded hits across project_source and base_source."""
        if self.is_bypassed:
            return self.base_source.recall_by_text(query, entry_kinds=entry_kinds, top_k=top_k)
        proj_hits = self.project_source.recall_by_text(
            query, entry_kinds=entry_kinds, top_k=max(top_k, 25)
        )
        base_hits = self.base_source.recall_by_text(
            query, entry_kinds=entry_kinds, top_k=max(top_k * 2, 25)
        )
        return self._fold_and_merge_hits(proj_hits, base_hits, top_k=top_k)

    def match_values_or_mentions(
        self,
        surface_forms: Sequence[str],
        *,
        pii_tags: Sequence[str] = (),
        context_hint: str | None = None,
        structural_patterns: Mapping[str, float] | None = None,
    ) -> list[RecallHit]:
        """Match surface values across project_source and base_source with Wave-1 folding."""
        if self.is_bypassed:
            return self.base_source.match_values_or_mentions(
                surface_forms,
                pii_tags=pii_tags,
                context_hint=context_hint,
                structural_patterns=structural_patterns,
            )
        proj_hits = self.project_source.match_values_or_mentions(
            surface_forms,
            pii_tags=pii_tags,
            context_hint=context_hint,
            structural_patterns=structural_patterns,
        )
        base_hits = self.base_source.match_values_or_mentions(
            surface_forms,
            pii_tags=pii_tags,
            context_hint=context_hint,
            structural_patterns=structural_patterns,
        )
        return self._fold_and_merge_hits(proj_hits, base_hits, top_k=25)

    def get_item(self, entry_id: str) -> KnowledgeItem | None:
        """Fetch project item enriched with mapped base_source item details, or base item."""
        if self.is_bypassed:
            return self.base_source.get_item(entry_id)
        proj_item = self.project_source.get_item(entry_id)
        if proj_item is None:
            base_item = self.base_source.get_item(entry_id)
            base_label = base_item.label if base_item is not None else ""
            if self.project_source.is_muted(entry_id, base_label):
                return None
            mapped_proj = self.project_source.standard_to_project_uri.get(entry_id)
            if mapped_proj is not None:
                mapped_item = self.project_source.get_item(mapped_proj)
                if mapped_item is not None and self.project_source.is_muted(
                    mapped_proj, mapped_item.label
                ):
                    return None
            return base_item
        if self.project_source.is_muted(entry_id, proj_item.label):
            return None

        mapped_stds = self.project_source.project_to_standard_uris.get(entry_id, ())
        for std_uri in mapped_stds:
            base_item = self.base_source.get_item(std_uri)
            if base_item is not None:
                return proj_item.model_copy(
                    update={
                        "aliases": dedupe_str_list([*proj_item.aliases, *base_item.aliases]),
                        "parent_ids": dedupe_str_list(
                            [*proj_item.parent_ids, *base_item.parent_ids]
                        ),
                        "replaces_uris": dedupe_str_list(
                            [*proj_item.replaces_uris, *base_item.replaces_uris]
                        ),
                    }
                )
        return proj_item

    def get_ancestors(self, entry_id: str, *, max_depth: int = 8) -> list[str]:
        """Combine project-ontology ancestors and mapped base-source ancestors."""
        if self.is_bypassed:
            return self.base_source.get_ancestors(entry_id, max_depth=max_depth)
        combined: list[str] = []
        combined.extend(self.project_source.get_ancestors(entry_id, max_depth=max_depth))
        for std_uri in self.project_source.project_to_standard_uris.get(entry_id, ()):
            combined.append(std_uri)
            combined.extend(self.base_source.get_ancestors(std_uri, max_depth=max_depth))
        combined.extend(self.base_source.get_ancestors(entry_id, max_depth=max_depth))
        deduped = [a for a in dedupe_str_list(combined) if a != entry_id]
        return deduped[:max_depth]

    def _translate_table_context(
        self,
        table_context: TableReasoningContext | None,
    ) -> TableReasoningContext | None:
        if table_context is None:
            return None
        proj_to_std = self.project_source.project_to_standard_uris
        expanded_anchors: list[TableAnchorCandidate] = []
        seen_anchor_ids: set[str] = set()
        for anchor in table_context.anchor_hypotheses:
            if anchor.entry_id not in seen_anchor_ids:
                expanded_anchors.append(anchor)
                seen_anchor_ids.add(anchor.entry_id)
            for std_uri in proj_to_std.get(anchor.entry_id, ()):
                if std_uri not in seen_anchor_ids:
                    expanded_anchors.append(
                        anchor.model_copy(
                            update={
                                "entry_id": std_uri,
                                "origin_kind": "reference_standard",
                            }
                        )
                    )
                    seen_anchor_ids.add(std_uri)

        translated_fks: dict[str, str] = {}
        for col_name, target_uri in table_context.fk_targets.items():
            std_list = proj_to_std.get(target_uri, ())
            translated_fks[col_name] = std_list[0] if std_list else target_uri

        return table_context.model_copy(
            update={
                "anchor_hypotheses": expanded_anchors,
                "fk_targets": translated_fks,
            }
        )

    def evaluate_structured_candidates(
        self,
        column: ColumnEvidence,
        profile: InferredValueProfile,
        candidates: Sequence[RecallHit],
        *,
        table_context: TableReasoningContext | None = None,
    ) -> list[BackendReasoningResult]:
        """Evaluate candidates by forwarding Wave-1 mapped URIs to base_source."""
        if self.is_bypassed:
            return self.base_source.evaluate_structured_candidates(
                column, profile, candidates, table_context=table_context
            )

        translated_ctx = self._translate_table_context(table_context)
        proj_to_std = self.project_source.project_to_standard_uris

        base_query_hits: list[RecallHit] = []
        std_to_orig_ids: dict[str, list[str]] = {}
        for hit in candidates:
            mapped_stds = proj_to_std.get(hit.entry_id, ())
            if mapped_stds:
                for std_uri in mapped_stds:
                    std_to_orig_ids.setdefault(std_uri, []).append(hit.entry_id)
                    base_query_hits.append(
                        hit.model_copy(
                            update={
                                "entry_id": std_uri,
                                "origin_kind": "reference_standard",
                            }
                        )
                    )
            else:
                base_query_hits.append(hit)

        base_results = self.base_source.evaluate_structured_candidates(
            column, profile, base_query_hits, table_context=translated_ctx
        )
        proj_results = self.project_source.evaluate_structured_candidates(
            column, profile, candidates, table_context=table_context
        )

        by_entry: dict[str, BackendReasoningResult] = {res.entry_id: res for res in proj_results}
        for b_res in base_results:
            target_ids = std_to_orig_ids.get(b_res.entry_id, [b_res.entry_id])
            for orig_id in target_ids:
                existing = by_entry.get(orig_id)
                if existing is None:
                    by_entry[orig_id] = b_res.model_copy(update={"entry_id": orig_id})
                else:
                    merged_ctx = b_res.semantic_context or existing.semantic_context
                    by_entry[orig_id] = b_res.model_copy(
                        update={
                            "entry_id": orig_id,
                            "hard_veto": existing.hard_veto or b_res.hard_veto,
                            "semantic_context": merged_ctx,
                        }
                    )

        return [by_entry[h.entry_id] for h in candidates if h.entry_id in by_entry]

    def score(self, query: str, entry_id: str) -> Decimal:
        """Return max normalized score across project_source and mapped base_source entries."""
        if self.is_bypassed:
            return self.base_source.score(query, entry_id)
        item = self.project_source.get_item(entry_id)
        if item is not None and self.project_source.is_muted(entry_id, item.label):
            return _ZERO_DEC
        base_item = self.base_source.get_item(entry_id)
        base_label = base_item.label if base_item is not None else ""
        if self.project_source.is_muted(entry_id, base_label):
            return _ZERO_DEC
        mapped_proj = self.project_source.standard_to_project_uri.get(entry_id)
        if mapped_proj is not None:
            mapped_item = self.project_source.get_item(mapped_proj)
            if mapped_item is not None and self.project_source.is_muted(
                mapped_proj, mapped_item.label
            ):
                return _ZERO_DEC
        scores = [
            self.project_source.score(query, entry_id),
            self.base_source.score(query, entry_id),
        ]
        for std_uri in self.project_source.project_to_standard_uris.get(entry_id, ()):
            scores.append(self.base_source.score(query, std_uri))
        return max(scores)


__all__ = [
    "OverlayKnowledgeSource",
    "ProjectGraphASource",
]
