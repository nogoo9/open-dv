"""Deterministic fallback construction engine (Task 5A.2 / Issue #212).

Conforms to docs/19 §5.3, docs/21 Task 5A.2, and ADR-002:
- Zero-API-key offline construction from evidence chunks and tabular structures.
- Schema Heuristics Engine:
    - Discrete text / `_code` / `_status` -> `AddReferenceEnumerationOp` (`ref_<slug>`), with codes.
    - Remaining `_id`, `_pk`, `_key` -> `AddConceptOp` (Hub CBCs, `hub_<slug>`).
    - Co-occurring key columns -> `AddRelationshipOp` (Links, `lnk_<e1>_<e2>`).
    - Non-key columns -> `AddAttributeOp` (Satellites).
- Pinned BM25 grounding lookup against EDM Council FIBO.
- Zero-waiver policy holding ungrounded candidates in `evidence/staged_candidates.json`.
- Dependency Closure Guard transitively withholding Links/Sats of ungrounded Hubs.
- Grandfathered Seed Concept Guard withholding ungrounded mutations.
- Physical identifier budget guard capping slugs at 55 characters.
- Fast execution (< 2.5s) with exit 0.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from opendv_contracts.dtos.ingestion import EvidenceChunk, TableStructure
from opendv_contracts.dtos.ontology import (
    AddAttributeOp,
    AddConceptOp,
    AddExternalMappingOp,
    AddReferenceEnumCodeOp,
    AddReferenceEnumerationOp,
    AddRelationshipOp,
    BaseOntologyDeltaOp,
    OntologyDelta,
    OntologyOperation,
    OntologyProposal,
)
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.construction.models import (
    ConstructOptions,
    StagedCandidate,
    StagedCandidatesFile,
)
from opendv_service.construction.utils import atomic_write_text
from opendv_service.ontology.matching.bm25 import ConceptMatcher
from opendv_service.ontology.matching.tokenization import STOPWORDS
from opendv_service.ontology.matching.verifier import (
    FiboPackGroundingVerifier,
)

logger = logging.getLogger(__name__)

MAX_PHYSICAL_SLUG_CHARS: int = 55
MAX_ENUM_CARDINALITY: int = 20


def _to_slug(name: str) -> str:
    """Normalize a column or concept name into an identifier slug."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", s).lower()
    return re.sub(r"_+", "_", s).strip("_")


def _is_numeric(val: str) -> bool:
    """Check if string value is numeric."""
    try:
        float(val.replace(",", ""))
        return True
    except ValueError:
        return False


def _det_id(*parts: str) -> str:
    """Generate deterministic 16-character hex identifier from unambiguous NUL-separated parts."""
    payload = "\x00".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _bump_minor_version(base_ver: str) -> str:
    """Compute minor version bump (e.g. '1.0.0' -> '1.1.0') for additive deltas."""
    if not base_ver or base_ver == "0.0.0":
        return "1.0.0"
    parts = base_ver.split(".")
    if len(parts) >= 2:
        try:
            major = int(parts[0])
            minor = int(parts[1])
            return f"{major}.{minor + 1}.0"
        except ValueError:
            pass
    return f"{base_ver}.1"


class DeterministicConstructionPipeline:
    """Headless zero-API-key deterministic fallback construction pipeline."""

    def __init__(
        self,
        matcher: ConceptMatcher | None = None,
        verifier: FiboPackGroundingVerifier | None = None,
    ) -> None:
        if verifier is not None:
            self._verifier = verifier
            self._matcher = verifier.matcher
        elif matcher is not None:
            self._matcher = matcher
            self._verifier = None
        else:
            self._verifier = FiboPackGroundingVerifier()
            self._matcher = self._verifier.matcher

    @property
    def matcher(self) -> ConceptMatcher:
        return self._matcher

    @property
    def verifier(self) -> FiboPackGroundingVerifier | None:
        return self._verifier

    def construct_proposal(
        self,
        base_ontology: GraphAOntology,
        evidence_chunks: list[EvidenceChunk] | None = None,
        tables: list[TableStructure] | None = None,
        clarification_answers: dict[str, Any] | None = None,
        options: ConstructOptions | None = None,
    ) -> OntologyProposal:
        """Construct an additive OntologyProposal from tables and evidence chunks."""
        opts = options or ConstructOptions()

        if opts.mode not in ("auto", "deterministic"):
            raise ValueError(
                f"DeterministicConstructionPipeline only supports 'deterministic' or 'auto' mode, "
                f"got: {opts.mode}"
            )

        # Check OCC precondition if supplied (MIN-03 / ONT-EVO-003)
        base_sha = compute_ontology_sha256(base_ontology)
        if opts.if_match is not None and opts.if_match != base_sha:
            raise ValueError(
                f"OCC precondition failed: if_match='{opts.if_match}' does not match "
                f"base ontology SHA-256='{base_sha}' (ONT-EVO-003)"
            )

        if opts.auto_commit:
            raise NotImplementedError(
                "auto_commit is not supported in offline deterministic construction; "
                "proposals must be reviewed and committed via Platform API."
            )

        answers_dict = clarification_answers or {}
        preferred_map_input = (
            answers_dict.get("preferred_mappings", {}) if isinstance(answers_dict, dict) else {}
        )
        if not isinstance(preferred_map_input, dict):
            logger.warning(
                "clarification_answers['preferred_mappings'] must be a dict, got %r",
                type(preferred_map_input),
            )
            preferred_map_input = {}

        preferred_mappings: dict[str, str] = {
            **opts.preferred_mappings,
            **preferred_map_input,
        }

        staged_candidates: list[StagedCandidate] = []
        accepted_ops: list[OntologyOperation] = []

        # Track candidate concepts and their status
        # map: concept_slug -> {
        #     "uri": str, "name": str, "hub_slug": str, "score": float, "target_uri": str
        # }
        accepted_hubs: dict[str, dict[str, Any]] = {}
        withheld_hubs: dict[str, StagedCandidate] = {}

        candidate_links: list[dict[str, Any]] = []
        candidate_attributes: list[dict[str, Any]] = []

        existing_concept_uris = (
            {c.concept_uri for c in base_ontology.canonical_business_concepts}
            if base_ontology.canonical_business_concepts
            else set()
        )
        existing_target_hubs = (
            {c.target_hub for c in base_ontology.canonical_business_concepts if c.target_hub}
            if base_ontology.canonical_business_concepts
            else set()
        )
        existing_hub_to_uri: dict[str, str] = {
            c.target_hub: c.concept_uri
            for c in getattr(base_ontology, "canonical_business_concepts", None) or []
            if c.target_hub and c.concept_uri
        }
        existing_rel_names = {
            r.target_link
            for r in getattr(base_ontology, "canonical_business_relationships", None) or []
        }
        existing_rels: dict[str, tuple[str, str]] = {
            r.target_link: (r.source_concept_uri, r.target_concept_uri)
            for r in getattr(base_ontology, "canonical_business_relationships", None) or []
            if r.target_link
            and getattr(r, "source_concept_uri", None)
            and getattr(r, "target_concept_uri", None)
        }
        existing_enum_names = {
            e.target_reference_table
            for e in getattr(base_ontology, "reference_enumerations", None) or []
        }
        existing_attribute_keys = {
            (c.concept_uri, a.attribute_name)
            for c in getattr(base_ontology, "canonical_business_concepts", None) or []
            for a in getattr(c, "attributes", None) or []
        }
        existing_enum_codes = {
            (e.target_reference_table, code)
            for e in getattr(base_ontology, "reference_enumerations", None) or []
            for code in getattr(e, "allowed_codes", None) or []
        }
        existing_mappings = {
            (c.concept_uri, m.target_uri)
            for c in getattr(base_ontology, "canonical_business_concepts", None) or []
            for m in getattr(c, "external_mappings", None) or []
        }

        seen_op_fingerprints: dict[str, str] = {}

        def emit_op(op: BaseOntologyDeltaOp) -> None:
            fp = op.model_dump_json(exclude={"op_id"})
            if op.op_id in seen_op_fingerprints:
                if seen_op_fingerprints[op.op_id] != fp:
                    raise ValueError(
                        f"Deterministic op_id collision detected for op_id='{op.op_id}' "
                        f"with differing content: existing={seen_op_fingerprints[op.op_id]} "
                        f"vs incoming={fp}"
                    )
                return  # Identical duplicate op, safely skip
            seen_op_fingerprints[op.op_id] = fp
            accepted_ops.append(op)

        # Step 1: Ingest TableStructures
        table_list: list[TableStructure] = list(tables or [])
        if evidence_chunks:
            for chk in evidence_chunks:
                if chk.is_table and "table_data" in chk.metadata:
                    try:
                        table_list.append(TableStructure.model_validate(chk.metadata["table_data"]))
                    except (ValidationError, KeyError, TypeError, ValueError) as err:
                        logger.debug("Failed to deserialize table from chunk metadata: %s", err)

        seen_enum_uris: set[str] = set()
        seen_enum_sources: dict[str, tuple[str, str]] = {}
        seen_key_pairs: set[frozenset[str]] = set()

        for tbl in table_list:
            headers = tbl.headers or []
            rows = tbl.rows or []
            tbl_id = tbl.table_id or "table"

            # First pass: identify key columns and determine table_hub_slug
            table_key_slugs: list[str] = []
            for col in headers:
                col_clean = col.strip()
                slug = _to_slug(col_clean)
                if slug:
                    if slug == "id":
                        tbl_slug = (
                            _to_slug(tbl.table_id or "")
                            .removeprefix("tbl_")
                            .removeprefix("table_")
                            .strip("_")
                        )
                        if tbl_slug:
                            table_key_slugs.append(tbl_slug)
                    elif slug.endswith(("_id", "_pk", "_key")):
                        entity_slug = (
                            slug.removesuffix("_id")
                            .removesuffix("_pk")
                            .removesuffix("_key")
                            .strip("_")
                        )
                        if entity_slug:
                            table_key_slugs.append(entity_slug)

            # Deduplicate table_key_slugs while preserving order (MIN-06)
            table_key_slugs = list(dict.fromkeys(table_key_slugs))
            table_hub_slug: str | None = table_key_slugs[0] if len(table_key_slugs) == 1 else None

            # Second pass: process non-key columns with exact column index
            for col_idx, col in enumerate(headers):
                col_clean = col.strip()
                slug = _to_slug(col_clean)
                if not slug or slug.endswith(("_id", "_pk", "_key")) or slug == "id":
                    continue

                # Heuristic 2: Low-cardinality discrete text / _code / _status -> Reference Enum
                is_enum_col = False
                col_values: list[str] = []

                distinct_vals: set[str] = set()
                if rows:
                    distinct_vals = {
                        str(row[col_idx]).strip()
                        for row in rows
                        if col_idx < len(row) and row[col_idx] is not None
                    }
                    distinct_vals = {v for v in distinct_vals if v}

                if slug.endswith(("_code", "_status", "_type", "_category")):
                    if len(distinct_vals) > MAX_ENUM_CARDINALITY:
                        # Cardinality cap exceeded: stage for steward review
                        # to prevent delta flooding
                        staged_candidates.append(
                            StagedCandidate(
                                candidate_id=f"staged-ref-{tbl_id}-{slug}",
                                concept_name=col_clean,
                                suggested_hub=f"ref_{slug}",
                                reason_held_back="ENUM_CARDINALITY_EXCEEDED",
                            )
                        )
                        continue
                    is_enum_col = True
                    col_values = sorted(distinct_vals)
                elif (
                    rows
                    and len(rows) >= 4
                    and 2 <= len(distinct_vals) <= min(10, MAX_ENUM_CARDINALITY)
                    and len(distinct_vals) <= len(rows) * 0.5
                    and not any(_is_numeric(v) for v in distinct_vals)
                    and all(len(v) <= 30 for v in distinct_vals)
                ):
                    is_enum_col = True
                    col_values = sorted(distinct_vals)

                if is_enum_col:
                    col_values = sorted({v for v in col_values if v})
                    # Strip only _code so that account_type and account_status
                    # remain distinct without merging codes (MAJ-03)
                    ref_slug = slug.removesuffix("_code")
                    ref_name = f"ref_{ref_slug}"
                    enum_uri = f"urn:ontology:enum:{ref_slug}"

                    # Detect enum name collision across different column names (MAJ-03)
                    if ref_name in seen_enum_sources and seen_enum_sources[ref_name] != slug:
                        staged_candidates.append(
                            StagedCandidate(
                                candidate_id=f"staged-ref-{slug}",
                                concept_name=col_clean,
                                suggested_hub=ref_name,
                                reason_held_back="REFERENCE_ENUM_IDENTIFIER_COLLISION",
                                evidence_refs=[{"column": col_clean, "table_id": tbl_id}],
                            )
                        )
                        continue
                    seen_enum_sources[ref_name] = slug

                    if len(ref_name) > MAX_PHYSICAL_SLUG_CHARS:
                        staged_candidates.append(
                            StagedCandidate(
                                candidate_id=f"staged-ref-{slug}",
                                concept_name=col_clean,
                                suggested_hub=ref_name,
                                reason_held_back="PHYSICAL_IDENTIFIER_BUDGET_EXCEEDED",
                                evidence_refs=[{"column": col_clean, "table_id": tbl_id}],
                            )
                        )
                        continue

                    if enum_uri not in seen_enum_uris and ref_name not in existing_enum_names:
                        seen_enum_uris.add(enum_uri)
                        emit_op(
                            AddReferenceEnumerationOp(
                                op_id=f"op-ref-{_det_id('enum', ref_name)}",
                                enum_uri=enum_uri,
                                name=ref_name,
                                target_reference_table=ref_name,
                                allowed_codes=[],
                            )
                        )

                    for val in col_values:
                        if (ref_name, val) not in existing_enum_codes:
                            emit_op(
                                AddReferenceEnumCodeOp(
                                    op_id=f"op-code-{_det_id('code', ref_name, val)}",
                                    enumeration_name=ref_name,
                                    code=val,
                                    description=val.replace("_", " ").title(),
                                )
                            )
                    continue

                # Heuristic 3: Non-key descriptive columns -> Satellite attributes
                candidate_attributes.append(
                    {
                        "table_id": tbl_id,
                        "table_hub": table_hub_slug,
                        "table_keys": list(table_key_slugs),
                        "attr_name": slug,
                        "col_name": col_clean,
                    }
                )

            # Heuristic 4: Co-occurring keys in table -> Links
            if len(table_key_slugs) >= 2:
                for i in range(len(table_key_slugs)):
                    for j in range(i + 1, len(table_key_slugs)):
                        src_k = table_key_slugs[i]
                        tgt_k = table_key_slugs[j]
                        if src_k != tgt_k:
                            pair_key = frozenset([src_k, tgt_k])
                            if pair_key in seen_key_pairs:
                                continue
                            seen_key_pairs.add(pair_key)
                            candidate_links.append(
                                {
                                    "source_slug": src_k,
                                    "target_slug": tgt_k,
                                    "table_id": tbl_id,
                                }
                            )

            # Record Hub candidates discovered from table keys
            for k_slug in table_key_slugs:
                if k_slug not in accepted_hubs and k_slug not in withheld_hubs:
                    self._evaluate_hub_candidate(
                        hub_slug=k_slug,
                        base_ontology=base_ontology,
                        existing_concept_uris=existing_concept_uris,
                        existing_target_hubs=existing_target_hubs,
                        existing_hub_to_uri=existing_hub_to_uri,
                        preferred_mappings=preferred_mappings,
                        min_confidence=opts.min_confidence,
                        accepted_hubs=accepted_hubs,
                        withheld_hubs=withheld_hubs,
                        staged_candidates=staged_candidates,
                    )

        # Step 2: Also evaluate evidence text chunks for explicit candidate entities if no tables
        if not table_list and evidence_chunks:
            for chk in evidence_chunks:
                phrases = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", chk.text)
                for phrase in phrases:
                    phrase_clean = phrase[4:].strip() if phrase.startswith("The ") else phrase
                    words = phrase_clean.split()
                    if len(words) == 1 and words[0].lower() in STOPWORDS:
                        continue
                    slug = _to_slug(phrase_clean)
                    if (
                        slug
                        and len(slug) >= 3
                        and slug not in accepted_hubs
                        and slug not in withheld_hubs
                    ):
                        self._evaluate_hub_candidate(
                            hub_slug=slug,
                            base_ontology=base_ontology,
                            existing_concept_uris=existing_concept_uris,
                            existing_target_hubs=existing_target_hubs,
                            existing_hub_to_uri=existing_hub_to_uri,
                            preferred_mappings=preferred_mappings,
                            min_confidence=opts.min_confidence,
                            accepted_hubs=accepted_hubs,
                            withheld_hubs=withheld_hubs,
                            staged_candidates=staged_candidates,
                        )

        # Step 3: Emit accepted Hub concepts & mappings
        for _hub_slug, hub_info in accepted_hubs.items():
            concept_uri = hub_info["uri"]
            concept_name = hub_info["name"]
            hub_name = hub_info["hub_slug"]
            if concept_uri not in existing_concept_uris and hub_name not in existing_target_hubs:
                emit_op(
                    AddConceptOp(
                        op_id=f"op-hub-{_det_id('concept', concept_uri, concept_name, hub_name)}",
                        concept_uri=concept_uri,
                        concept_name=concept_name,
                        target_hub=hub_name,
                    )
                )
            target_uri = hub_info.get("target_uri")
            score = hub_info.get("score", 1.0)
            if target_uri and (concept_uri, target_uri) not in existing_mappings:
                pred = "skos:exactMatch" if score >= 0.8500 else "skos:closeMatch"
                map_op_id = (
                    f"op-map-{_det_id('mapping', concept_uri, target_uri, pred, f'{score:.4f}')}"
                )
                emit_op(
                    AddExternalMappingOp(
                        op_id=map_op_id,
                        concept_uri=concept_uri,
                        target_uri=target_uri,
                        scheme="fibo",
                        confidence=score,
                        predicate=pred,
                    )
                )

        # Step 4: Evaluate Dependency Closure Guard on Links
        seen_link_names: dict[str, tuple[str, str]] = {}
        for lnk in candidate_links:
            src = lnk["source_slug"]
            tgt = lnk["target_slug"]
            lnk_name = f"lnk_{src}_{tgt}"

            # Link name collision guard (MAJ-03): same link name from different source/target pairs
            if lnk_name in seen_link_names and seen_link_names[lnk_name] != (src, tgt):
                staged_candidates.append(
                    StagedCandidate(
                        candidate_id=f"staged-lnk-{src}-{tgt}",
                        concept_name=f"{src.title()} to {tgt.title()}",
                        suggested_hub=lnk_name,
                        reason_held_back="LINK_IDENTIFIER_COLLISION",
                    )
                )
                continue
            seen_link_names[lnk_name] = (src, tgt)

            # Check collision with existing base relationships (MIN-04)
            if lnk_name in existing_rels:
                src_uri = accepted_hubs[src]["uri"] if src in accepted_hubs else None
                tgt_uri = accepted_hubs[tgt]["uri"] if tgt in accepted_hubs else None
                if src_uri and tgt_uri:
                    existing_endpoints = existing_rels[lnk_name]
                    if existing_endpoints != (src_uri, tgt_uri):
                        staged_candidates.append(
                            StagedCandidate(
                                candidate_id=f"staged-lnk-{src}-{tgt}",
                                concept_name=f"{src.title()} to {tgt.title()}",
                                suggested_hub=lnk_name,
                                reason_held_back="LINK_IDENTIFIER_COLLISION",
                            )
                        )
                continue

            # Budget guard on link name
            if len(lnk_name) > MAX_PHYSICAL_SLUG_CHARS:
                staged_candidates.append(
                    StagedCandidate(
                        candidate_id=f"staged-lnk-{src}-{tgt}",
                        concept_name=f"{src.title()} to {tgt.title()}",
                        suggested_hub=lnk_name,
                        reason_held_back="PHYSICAL_IDENTIFIER_BUDGET_EXCEEDED",
                    )
                )
                continue

            # If either endpoint was held back, link is transitively held back
            if src in withheld_hubs or tgt in withheld_hubs:
                staged_candidates.append(
                    StagedCandidate(
                        candidate_id=f"staged-lnk-{src}-{tgt}",
                        concept_name=f"{src.title()} to {tgt.title()}",
                        suggested_hub=lnk_name,
                        reason_held_back="DEPENDENCY_CLOSURE_UNMET",
                    )
                )
                continue

            if src in accepted_hubs and tgt in accepted_hubs and lnk_name not in existing_rel_names:
                emit_op(
                    AddRelationshipOp(
                        op_id=f"op-lnk-{_det_id('link', src, tgt)}",
                        relationship_uri=f"urn:ontology:relationship:{src}_{tgt}",
                        relationship_name=lnk_name,
                        target_link=lnk_name,
                        source_concept_uri=accepted_hubs[src]["uri"],
                        target_concept_uri=accepted_hubs[tgt]["uri"],
                        endpoints=[accepted_hubs[src]["uri"], accepted_hubs[tgt]["uri"]],
                    )
                )

        # Step 5: Evaluate Dependency Closure Guard on Satellite attributes
        for attr in candidate_attributes:
            thub = attr.get("table_hub")
            tkeys = attr.get("table_keys") or []
            attr_name = attr["attr_name"]
            table_id = attr.get("table_id") or "table"
            attr_cand_id = f"staged-attr-{attr_name}"

            # Physical identifier budget guard on attribute name
            if len(attr_name) > MAX_PHYSICAL_SLUG_CHARS:
                staged_candidates.append(
                    StagedCandidate(
                        candidate_id=attr_cand_id,
                        concept_name=attr["col_name"],
                        suggested_hub=f"hub_{thub}" if thub else "link_satellite",
                        reason_held_back="PHYSICAL_IDENTIFIER_BUDGET_EXCEEDED",
                        evidence_refs=[{"column": attr["col_name"], "table_id": table_id}],
                    )
                )
                continue

            if thub and thub in accepted_hubs:
                hub_uri = accepted_hubs[thub]["uri"]
                if (hub_uri, attr_name) not in existing_attribute_keys:
                    emit_op(
                        AddAttributeOp(
                            op_id=f"op-attr-{_det_id('attr', hub_uri, attr_name)}",
                            concept_uri=hub_uri,
                            attribute_name=attr_name,
                            data_type="string",
                        )
                    )
            elif thub and thub in withheld_hubs:
                # Withhold attribute transitively with the parent Hub
                withheld_cand = withheld_hubs[thub]
                attr_dict = {
                    "attribute_name": attr_name,
                    "column": attr.get("col_name", attr_name),
                    "table_id": table_id,
                }
                if not any(
                    isinstance(a, dict) and a.get("attribute_name") == attr_name
                    for a in withheld_cand.attributes
                ):
                    withheld_cand.attributes.append(attr_dict)
            elif len(tkeys) >= 2:
                # Multi-key table (e.g. link or intersection table)
                if any(k in withheld_hubs for k in tkeys):
                    staged_candidates.append(
                        StagedCandidate(
                            candidate_id=attr_cand_id,
                            concept_name=attr["col_name"],
                            suggested_hub="link_satellite",
                            reason_held_back="DEPENDENCY_CLOSURE_UNMET",
                            evidence_refs=[{"column": attr["col_name"], "table_id": table_id}],
                        )
                    )
                else:
                    staged_candidates.append(
                        StagedCandidate(
                            candidate_id=attr_cand_id,
                            concept_name=attr["col_name"],
                            suggested_hub="link_satellite",
                            reason_held_back="LINK_SATELLITE_REQUIRES_CLARIFICATION",
                            evidence_refs=[{"column": attr["col_name"], "table_id": table_id}],
                        )
                    )
            else:
                # Orphan attribute without grounded parent Hub
                staged_candidates.append(
                    StagedCandidate(
                        candidate_id=attr_cand_id,
                        concept_name=attr["col_name"],
                        suggested_hub=f"hub_{thub}" if thub else "unknown",
                        reason_held_back="ORPHAN_ATTRIBUTE_NO_HUB",
                        evidence_refs=[{"column": attr["col_name"], "table_id": table_id}],
                    )
                )

        # Deduplicate staged candidates merging metadata preserving order (MIN-05)
        merged_staged: dict[str, StagedCandidate] = {}
        for cand in staged_candidates:
            if cand.candidate_id in merged_staged:
                existing = merged_staged[cand.candidate_id]
                for ev in cand.evidence_refs:
                    if ev not in existing.evidence_refs:
                        existing.evidence_refs.append(ev)
                for a in cand.attributes:
                    if a not in existing.attributes:
                        existing.attributes.append(a)
                for dl in cand.dependent_links:
                    if dl not in existing.dependent_links:
                        existing.dependent_links.append(dl)
            else:
                merged_staged[cand.candidate_id] = cand
        staged_candidates = list(merged_staged.values())

        # Step 6: Persist staged candidates holding area file
        if opts.session_dir:
            session_path = Path(opts.session_dir).resolve()
            evidence_dir = session_path / "evidence"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            staged_file_payload = StagedCandidatesFile(
                session_id=opts.session_id or "default-session",
                generated_at=datetime.now(UTC).isoformat(),
                pipeline_mode="deterministic",
                staged_candidates=staged_candidates,
            )
            staged_path = evidence_dir / "staged_candidates.json"
            atomic_write_text(staged_path, staged_file_payload.model_dump_json(indent=2))

        # Step 7: Assemble OntologyDelta and ProposalRecord
        base_ver = (
            base_ontology.ontology_version or getattr(base_ontology, "version", None) or "1.0.0"
        )
        target_ver = _bump_minor_version(base_ver)

        op_content_hash = hashlib.sha256(
            json.dumps(
                [op.model_dump(mode="json", exclude_none=True) for op in accepted_ops],
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:16]

        author = (
            f"{opts.actor}:{opts.principal}"
            if (opts.actor and opts.principal and opts.principal != "system:deterministic")
            else (opts.actor if opts.actor else "deterministic:pipeline")
        )

        parent_sha = "" if base_ver == "0.0.0" else base_sha
        delta = OntologyDelta(
            delta_id=f"delta-{_det_id('delta', base_sha, target_ver, op_content_hash)}",
            base_ontology_version=base_ver,
            target_ontology_version=target_ver,
            parent_sha256=parent_sha,
            domain_namespace=base_ontology.domain_namespace or "default",
            author=author,
            operations=accepted_ops,
            rationale="Automated zero-API-key deterministic fallback construction.",
        )

        proposal = OntologyProposal(
            proposal_id=f"prop-{_det_id('proposal', delta.delta_id)}",
            recommender_id="deterministic-pipeline",
            recommender_version="1.0.0",
            base_ontology_sha256=base_sha,
            delta=delta,
            confidence_score=1.0 if accepted_ops else 0.0,
            rationale="Deterministic fallback construction from evidence chunks and tables.",
            created_at_utc=datetime.now(UTC).isoformat(),
        )

        return proposal

    def _evaluate_hub_candidate(
        self,
        hub_slug: str,
        base_ontology: GraphAOntology,
        existing_concept_uris: set[str],
        existing_target_hubs: set[str],
        existing_hub_to_uri: dict[str, str],
        preferred_mappings: dict[str, str],
        min_confidence: float,
        accepted_hubs: dict[str, dict[str, Any]],
        withheld_hubs: dict[str, StagedCandidate],
        staged_candidates: list[StagedCandidate],
    ) -> None:
        """Evaluate a candidate Hub concept against grounding rules and guards."""
        hub_name = f"hub_{hub_slug}"
        concept_uri = existing_hub_to_uri.get(hub_name, f"urn:ontology:entity:{hub_slug}")
        concept_label = hub_slug.replace("_", " ").title()

        # Guard 1: Physical table identifier budget (<= 55 chars)
        if len(hub_name) > MAX_PHYSICAL_SLUG_CHARS:
            cand = StagedCandidate(
                candidate_id=f"staged-{hub_slug}",
                concept_name=concept_label,
                suggested_hub=hub_name,
                reason_held_back="PHYSICAL_IDENTIFIER_BUDGET_EXCEEDED",
            )
            withheld_hubs[hub_slug] = cand
            staged_candidates.append(cand)
            return

        # Guard 2: Human clarification answer override
        target_uri: str | None = None
        score: float = 0.0

        if hub_slug in preferred_mappings or concept_label in preferred_mappings:
            candidate_target_uri = preferred_mappings.get(hub_slug) or preferred_mappings.get(
                concept_label
            )
            is_verified = False
            if (
                candidate_target_uri
                and self._matcher.lookup_concept(candidate_target_uri) is not None
            ):
                is_verified = True

            if is_verified and candidate_target_uri:
                target_uri = candidate_target_uri
                score = 1.0
            else:
                cand = StagedCandidate(
                    candidate_id=f"staged-{hub_slug}",
                    concept_name=concept_label,
                    suggested_hub=hub_name,
                    top_match_uri=candidate_target_uri,
                    top_match_score=0.0,
                    reason_held_back="PREFERRED_MAPPING_UNVERIFIED",
                )
                withheld_hubs[hub_slug] = cand
                staged_candidates.append(cand)
                return
        else:
            # Query in-memory BM25 index
            matches = self._matcher.match(concept_label, top_k=1)
            if matches:
                top_m = matches[0]
                score = top_m.normalized_score
                target_uri = top_m.concept_uri

        # Verifier binding: if a GroundingVerifier is attached, verify mapping (MAJ-01)
        if self._verifier is not None and target_uri:
            cand_mapping = {
                "target_uri": target_uri,
                "confidence": score,
                "predicate": "skos:exactMatch" if score >= 0.8500 else "skos:closeMatch",
            }
            v_res = self._verifier.verify_grounding(
                concept_uri=concept_uri,
                candidates=[cand_mapping],
                threshold=min_confidence,
                concept_name=concept_label,
            )
            if not v_res.is_grounded:
                cand = StagedCandidate(
                    candidate_id=f"staged-{hub_slug}",
                    concept_name=concept_label,
                    suggested_hub=hub_name,
                    top_match_uri=target_uri,
                    top_match_score=v_res.score,
                    reason_held_back="VERIFIER_REJECTED",
                )
                withheld_hubs[hub_slug] = cand
                staged_candidates.append(cand)
                return
            score = v_res.score

        # Guard 3: Grandfathered Seed Concept Guard
        # If concept already existed in base ontology (by URI or target_hub), mutations require
        # verified grounding >= 0.6500
        is_existing = (concept_uri in existing_concept_uris) or (hub_name in existing_target_hubs)
        if is_existing and score < min_confidence:
            cand = StagedCandidate(
                candidate_id=f"staged-{hub_slug}",
                concept_name=concept_label,
                suggested_hub=hub_name,
                top_match_uri=target_uri,
                top_match_score=score,
                reason_held_back="GRANDFATHERED_MUTATION_UNGROUNDED",
            )
            withheld_hubs[hub_slug] = cand
            staged_candidates.append(cand)
            return

        # Guard 4: Zero-waiver grounding threshold guard
        if score < min_confidence or not target_uri:
            cand = StagedCandidate(
                candidate_id=f"staged-{hub_slug}",
                concept_name=concept_label,
                suggested_hub=hub_name,
                top_match_uri=target_uri,
                top_match_score=score,
                reason_held_back="GROUNDING_THRESHOLD_UNMET",
            )
            withheld_hubs[hub_slug] = cand
            staged_candidates.append(cand)
            return

        # Candidate is accepted!
        accepted_hubs[hub_slug] = {
            "uri": concept_uri,
            "name": concept_label,
            "hub_slug": hub_name,
            "score": score,
            "target_uri": target_uri,
        }
