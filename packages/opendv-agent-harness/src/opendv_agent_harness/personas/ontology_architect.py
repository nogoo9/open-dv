"""`ontology-architect` Persona Builder: Deep Reasoning Construction Agent SPI."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opendv_agent_harness.models import (
    AgentPersona,
    AgentTaskSpec,
    AgentToolDefinition,
    ModelTier,
    OutputT,
    PipelineStageId,
    SubagentSpec,
)
from opendv_agent_harness.personas.domain_clarifier import (
    DomainClarifier,
    build_domain_clarifier_subagent_spec,
)
from opendv_agent_harness.personas.term_scout import (
    TermScout,
    build_term_scout_subagent_spec,
)
from opendv_agent_harness.privacy import (
    AgentDestructiveOpForbiddenError,
    AgentUnresolvedPseudonymError,
    DanglingParentReferenceError,
    Pseudonymizer,
    RedactedPlaceholderLeakError,
    scan_redacted_placeholder_leak,
    scan_unresolved_pseudonyms,
)
from opendv_agent_harness.routing import get_persona_capability
from opendv_agent_harness.tools.catalog import get_tools_for_persona
from opendv_contracts.dtos.clarification import ClarificationQuestion
from opendv_contracts.dtos.ingestion import EvidenceChunk, TableStructure
from opendv_contracts.dtos.ontology import (
    AddConceptOp,
    AddReferenceEnumerationOp,
    BaseOntologyDeltaOp,
    EvidenceRef,
    OntologyDelta,
    OntologyProposal,
)
from opendv_contracts.jcs import dumps_jcs
from opendv_contracts.spi.agent import ConstructionAgent
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256

ONTOLOGY_ARCHITECT_SYSTEM_INSTRUCTIONS: str = (
    "You are `@ontology-architect`. Propose strictly additive delta ops. "
    "Respect taint, prevent leaks, and resolve HMAC pseudonyms."
)

_ALLOWED_ADDITIVE_OP_TYPES: frozenset[str] = frozenset(
    {
        "add_concept",
        "add_relationship",
        "add_attribute",
        "add_reference_enumeration",
        "add_reference_enum_code",
        "add_external_mapping",
    }
)


class InvalidOntologyVersionError(ValueError):
    """Raised when base ontology version is not a valid SemVer string."""

    exit_code = 4
    http_status = 422
    error_code = "INVALID_ONTOLOGY_VERSION"


def _bump_minor_version(base_ver: str) -> str:
    """Bump minor version for additive deltas."""
    if not base_ver or base_ver == "0.0.0":
        return "1.0.0"
    parts = base_ver.split(".")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return f"{parts[0]}.{int(parts[1]) + 1}.0"
    raise InvalidOntologyVersionError(
        f"Invalid SemVer base version '{base_ver}', expected 'MAJOR.MINOR.PATCH'"
    )


def _clean_evidence_citations(
    citations: Sequence[EvidenceRef],
    pseudonymizer: Pseudonymizer | None = None,
) -> list[EvidenceRef]:
    """Sanitize evidence citations to prevent leaks and unresolved pseudonyms."""
    clean_citations: list[EvidenceRef] = []
    for cit in citations:
        snippet = cit.snippet
        ref_uri = cit.reference_uri

        if pseudonymizer and ref_uri:
            ref_uri = pseudonymizer.de_pseudonymize(ref_uri)

        if snippet:
            try:
                scan_redacted_placeholder_leak(snippet)
                scan_unresolved_pseudonyms(snippet)
            except (RedactedPlaceholderLeakError, AgentUnresolvedPseudonymError):
                snippet = None

        if ref_uri:
            try:
                scan_redacted_placeholder_leak(ref_uri)
                scan_unresolved_pseudonyms(ref_uri)
            except (RedactedPlaceholderLeakError, AgentUnresolvedPseudonymError):
                continue  # Drop citation if reference_uri cannot be cleaned (MIN-01)

        data = cit.model_dump()
        data["snippet"] = snippet
        data["reference_uri"] = ref_uri
        clean_citations.append(EvidenceRef.model_validate(data))
    return clean_citations


def _to_clean_slug(text: str) -> str:
    """Sanitize concept label into valid physical identifier slug."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", text.strip()).lower().strip("_")
    if len(cleaned) > 55:
        h = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:6]
        cleaned = f"{cleaned[:48]}_{h}"
    return cleaned


def build_ontology_architect_subagent_spec() -> SubagentSpec:
    """Build canonical `SubagentSpec` for `@ontology-architect`."""
    profile = get_persona_capability(AgentPersona.ONTOLOGY_ARCHITECT)
    tool_names = tuple(t.name for t in get_tools_for_persona(AgentPersona.ONTOLOGY_ARCHITECT))
    return SubagentSpec(
        name=AgentPersona.ONTOLOGY_ARCHITECT.value,
        persona=AgentPersona.ONTOLOGY_ARCHITECT,
        description="@ontology-architect reasoning tier SPI.",
        system_instructions=ONTOLOGY_ARCHITECT_SYSTEM_INSTRUCTIONS,
        model_tier=profile.default_model_tier,
        allowed_tool_names=tool_names,
        allowed_subagents=profile.allowed_subagents,
        read_only=profile.sandbox_policy.read_only_workspace,
    )


def build_ontology_architect_task_spec(
    prompt: str = "Synthesize additive ontology delta.",
    *,
    run_id: str = "run-ontology-architect",
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    task_id: str | None = None,
    stage_id: PipelineStageId = PipelineStageId.STAGE_00_ORCHESTRATION,
    model_tier: ModelTier | None = None,
    model_override: str | None = None,
    tools: Sequence[AgentToolDefinition[Any, Any]] | None = None,
    subagents: Sequence[SubagentSpec] | None = None,
    workspace_root: Path | None = None,
    input_data: Any = None,
    expected_output_type: type[OutputT] | None = None,
    system_instructions: str = ONTOLOGY_ARCHITECT_SYSTEM_INSTRUCTIONS,
) -> AgentTaskSpec[Any]:
    """Build canonical `AgentTaskSpec` for `@ontology-architect`."""
    profile = get_persona_capability(AgentPersona.ONTOLOGY_ARCHITECT)
    eff_trace = trace_id or uuid.uuid4().hex
    eff_tools = (
        tuple(tools)
        if tools is not None
        else tuple(get_tools_for_persona(AgentPersona.ONTOLOGY_ARCHITECT))
    )
    eff_subagents: tuple[SubagentSpec, ...] = (
        tuple(subagents)
        if subagents is not None
        else (
            build_term_scout_subagent_spec(),
            build_domain_clarifier_subagent_spec(),
        )
    )
    from opendv_agent_harness.personas import build_input_context_refs

    context_refs: dict[str, str | None] = {}
    if workspace_root is not None:
        context_refs["workspace_root"] = str(workspace_root)
    context_refs.update(build_input_context_refs(input_data))
    kwargs: dict[str, Any] = {
        "run_id": run_id,
        "trace_id": eff_trace,
        "parent_span_id": parent_span_id,
        "persona": AgentPersona.ONTOLOGY_ARCHITECT,
        "stage_id": stage_id,
        "prompt": prompt,
        "system_instructions": system_instructions,
        "model_tier": model_tier or profile.default_model_tier,
        "model_override": model_override,
        "tools": eff_tools,
        "subagents": eff_subagents,
        "context_refs": context_refs,
    }
    if task_id is not None:
        kwargs["task_id"] = task_id
    if expected_output_type is not None:
        kwargs["output_schema"] = expected_output_type
    return AgentTaskSpec(**kwargs)


class OntologyArchitect(ConstructionAgent):
    """Deep reasoning construction agent implementing `ConstructionAgent` SPI."""

    def __init__(
        self,
        pseudonymizer: Pseudonymizer | None = None,
        term_scout: TermScout | None = None,
        domain_clarifier: DomainClarifier | None = None,
    ) -> None:
        self.pseudonymizer = pseudonymizer
        self.term_scout = term_scout or TermScout(pseudonymizer=pseudonymizer)
        self.domain_clarifier = domain_clarifier or DomainClarifier(pseudonymizer=pseudonymizer)

    def propose_delta(
        self,
        base_ontology: Any,
        evidence: list[Any] | None = None,
        **kwargs: Any,
    ) -> OntologyProposal:
        """Synthesize additive ontology delta proposal implementing ConstructionAgent SPI."""
        # Validate base_ontology at top (MIN-07)
        if base_ontology is not None and not isinstance(base_ontology, GraphAOntology):
            raise TypeError(
                f"base_ontology must be GraphAOntology or None, got: {type(base_ontology)}"
            )

        # Validate explicit operations (MAJ-01, MIN-07)
        explicit_ops: list[BaseOntologyDeltaOp] | None = kwargs.get("operations")
        if explicit_ops is not None:
            for op in explicit_ops:
                if not isinstance(op, BaseOntologyDeltaOp):
                    raise TypeError(
                        f"All operations in kwargs must be BaseOntologyDeltaOp, got: {type(op)}"
                    )
            self._verify_additive_only(explicit_ops)

        # Separate evidence into chunks and tables
        chunks: list[EvidenceChunk] = []
        tables: list[TableStructure] = []
        if evidence:
            for item in evidence:
                if isinstance(item, EvidenceChunk):
                    chunks.append(item)
                elif isinstance(item, TableStructure):
                    tables.append(item)

        # Use @term-scout to harvest candidates
        candidates = self.term_scout.extract_candidates(chunks, tables)

        # Track existing entities in base ontology (MIN-02)

        existing_concepts: set[str] = set()
        base_concept_names: set[str] = set()
        base_hubs: set[str] = set()
        existing_enums: set[str] = set()
        base_enum_names: set[str] = set()

        if isinstance(base_ontology, GraphAOntology):
            for c in getattr(base_ontology, "canonical_business_concepts", None) or []:
                existing_concepts.add(c.concept_uri)
                if c.concept_name:
                    base_concept_names.add(c.concept_name.strip().lower())
                if c.target_hub:
                    base_hubs.add(c.target_hub.strip().lower())

            for e in getattr(base_ontology, "reference_enumerations", None) or []:
                if e.enum_uri:
                    existing_enums.add(e.enum_uri)
                if e.target_reference_table:
                    base_enum_names.add(e.target_reference_table.strip().lower())
                ename = getattr(e, "name", None) or getattr(e, "enumeration_name", None)
                if ename:
                    base_enum_names.add(ename.strip().lower())

        # Build candidate additive operations: explicit ops take priority (MAJ-01)
        operations: list[BaseOntologyDeltaOp] = []
        seen_op_keys: set[tuple[Any, ...]] = set()

        def _get_op_dedup_key(op: BaseOntologyDeltaOp) -> tuple[Any, ...]:
            op_type = getattr(op, "op_type", "")
            op_id = getattr(op, "op_id", "") or "op"
            if op_type == "add_concept":
                curi = getattr(op, "concept_uri", "")
                cname = getattr(op, "concept_name", "") or getattr(op, "target_hub", "")
                slug = curi.rsplit(":", 1)[-1] if curi else _to_clean_slug(cname)
                return ("add_concept", slug or op_id)
            elif op_type == "add_relationship":
                src = getattr(op, "source_concept_uri", "")
                rel_ident = (
                    getattr(op, "relationship_uri", "")
                    or getattr(op, "relationship_name", "")
                    or getattr(op, "target_link", "")
                    or op_id
                )
                tgt = getattr(op, "target_concept_uri", "")
                return ("add_relationship", src, rel_ident, tgt)
            elif op_type == "add_reference_enumeration":
                euri = getattr(op, "enum_uri", "")
                ename = (
                    getattr(op, "name", "") or getattr(op, "target_reference_table", "") or op_id
                )
                slug = (
                    euri.rsplit(":", 1)[-1] if euri else _to_clean_slug(ename).removeprefix("ref_")
                )
                return ("add_reference_enumeration", slug or op_id)
            elif op_type == "add_attribute":
                curi = getattr(op, "concept_uri", "") or op_id
                aname = getattr(op, "attribute_name", "") or op_id
                return ("add_attribute", curi, aname)
            elif op_type == "add_reference_enum_code":
                euri = getattr(op, "enum_uri", "")
                ename = getattr(op, "enumeration_name", "")
                parent_ident = euri or _to_clean_slug(ename).removeprefix("ref_") or op_id
                code = str(getattr(op, "code", "")) or op_id
                return ("add_reference_enum_code", parent_ident, code)
            elif op_type == "add_external_mapping":
                curi = getattr(op, "concept_uri", "") or op_id
                std = getattr(op, "scheme", "") or getattr(op, "standard", "") or ""
                tgt = getattr(op, "target_uri", "") or op_id
                pred = getattr(op, "predicate", "") or ""
                return ("add_external_mapping", curi, std, tgt, pred)
            return (op_type, op_id)

        explicit_concept_uris: set[str] = set()
        explicit_concept_uri_remapping: dict[str, str] = {}
        explicit_enum_uris: set[str] = set()
        explicit_enum_uri_remapping: dict[str, str] = {}
        delta_concept_names: set[str] = set()
        delta_hubs: set[str] = set()
        delta_enum_names: set[str] = set()

        def _is_grounded(refs: Sequence[Any] | None) -> bool:
            if not refs:
                return False
            return any(
                getattr(r, "reference_uri", None)
                or getattr(r, "snippet", None)
                or getattr(r, "doc_sha256", None)
                for r in refs
            )

        # Step 1: Add caller-supplied explicit operations first (MAJ-01)
        if explicit_ops:
            for ex_op in explicit_ops:
                op_type = getattr(ex_op, "op_type", None)
                if op_type == "add_concept":
                    curi = getattr(ex_op, "concept_uri", "")
                    cname = getattr(ex_op, "concept_name", "")
                    ev_refs = getattr(ex_op, "evidence_refs", None) or []
                    slug = curi.rsplit(":", 1)[-1] if curi else (_to_clean_slug(cname) or "concept")
                    if not curi:
                        curi = f"urn:ontology:provisional:entity:{slug}"
                        ex_op = type(ex_op).model_validate(
                            ex_op.model_dump() | {"concept_uri": curi}
                        )
                    canon_uri = f"urn:ontology:entity:{slug}"
                    prov_uri = f"urn:ontology:provisional:entity:{slug}"

                    # Canonical / provisional twin check & base match
                    is_base = (
                        curi in existing_concepts
                        or canon_uri in existing_concepts
                        or prov_uri in existing_concepts
                    )
                    if is_base:
                        base_uri = (
                            canon_uri
                            if canon_uri in existing_concepts
                            else (prov_uri if prov_uri in existing_concepts else curi)
                        )
                        explicit_concept_uri_remapping[curi] = base_uri
                        continue

                    # Check if already added in this delta (keep distinct URIs, skip exact dup)
                    if curi in explicit_concept_uris or prov_uri in explicit_concept_uris:
                        continue

                    # Grounding / Provisional namespace enforcement (ONT-EVO-002)
                    if (
                        curi
                        and not curi.startswith("urn:ontology:provisional:")
                        and not _is_grounded(ev_refs)
                    ):
                        prov_target = f"urn:ontology:provisional:entity:{slug}"
                        explicit_concept_uri_remapping[curi] = prov_target
                        ex_op = type(ex_op).model_validate(
                            ex_op.model_dump() | {"concept_uri": prov_target}
                        )
                        curi = prov_target

                elif op_type == "add_reference_enumeration":
                    euri = getattr(ex_op, "enum_uri", "")
                    ename = getattr(ex_op, "name", "") or getattr(
                        ex_op, "target_reference_table", ""
                    )
                    ev_refs = getattr(ex_op, "evidence_refs", None) or []
                    if euri:
                        slug = euri.rsplit(":", 1)[-1]
                    elif ename:
                        slug = _to_clean_slug(ename).removeprefix("ref_")
                    else:
                        slug = "enum"
                    if not euri:
                        euri = f"urn:ontology:provisional:enum:{slug}"
                        ex_op = type(ex_op).model_validate(ex_op.model_dump() | {"enum_uri": euri})
                    canon_uri = f"urn:ontology:enum:{slug}"
                    prov_uri = f"urn:ontology:provisional:enum:{slug}"

                    # Canonical / provisional twin check & base match
                    is_base_enum = (
                        euri in existing_enums
                        or canon_uri in existing_enums
                        or prov_uri in existing_enums
                    )
                    if is_base_enum:
                        base_uri = (
                            canon_uri
                            if canon_uri in existing_enums
                            else (prov_uri if prov_uri in existing_enums else euri)
                        )
                        explicit_enum_uri_remapping[euri] = base_uri
                        continue

                    if euri in explicit_enum_uris or prov_uri in explicit_enum_uris:
                        continue

                    # Grounding / Provisional namespace enforcement (ONT-EVO-002)
                    if (
                        euri
                        and not euri.startswith("urn:ontology:provisional:")
                        and not _is_grounded(ev_refs)
                    ):
                        prov_target = f"urn:ontology:provisional:enum:{slug}"
                        explicit_enum_uri_remapping[euri] = prov_target
                        ex_op = type(ex_op).model_validate(
                            ex_op.model_dump() | {"enum_uri": prov_target}
                        )
                        euri = prov_target

                elif op_type == "add_relationship":
                    ruri = getattr(ex_op, "relationship_uri", "")
                    ev_refs = getattr(ex_op, "evidence_refs", None) or []
                    # Grounding / Provisional namespace enforcement (MAJ-02 ONT-EVO-002)
                    if (
                        ruri
                        and not ruri.startswith("urn:ontology:provisional:")
                        and not _is_grounded(ev_refs)
                    ):
                        if ruri.startswith("urn:ontology:relationship:"):
                            prov_target = ruri.replace(
                                "urn:ontology:relationship:",
                                "urn:ontology:provisional:relationship:",
                            )
                        elif ruri.startswith("urn:ontology:event:"):
                            prov_target = ruri.replace(
                                "urn:ontology:event:",
                                "urn:ontology:provisional:event:",
                            )
                        else:
                            rel_slug = ruri.rsplit(":", 1)[-1]
                            prov_target = f"urn:ontology:provisional:relationship:{rel_slug}"
                        ex_op = type(ex_op).model_validate(
                            ex_op.model_dump() | {"relationship_uri": prov_target}
                        )

                key = _get_op_dedup_key(ex_op)
                if key not in seen_op_keys:
                    seen_op_keys.add(key)
                    operations.append(ex_op)
                    if op_type == "add_concept":
                        curi = getattr(ex_op, "concept_uri", "")
                        if curi:
                            explicit_concept_uris.add(curi)
                        c_name = getattr(ex_op, "concept_name", "")
                        if c_name:
                            delta_concept_names.add(c_name.strip().lower())
                        hub = getattr(ex_op, "target_hub", "")
                        if hub:
                            delta_hubs.add(hub.strip().lower())
                    elif op_type == "add_reference_enumeration":
                        euri = getattr(ex_op, "enum_uri", "")
                        ename = getattr(ex_op, "name", "") or getattr(
                            ex_op, "target_reference_table", ""
                        )
                        if euri:
                            explicit_enum_uris.add(euri)
                        if ename:
                            delta_enum_names.add(ename.strip().lower())
                        ref_tbl = getattr(ex_op, "target_reference_table", "")
                        if ref_tbl:
                            delta_enum_names.add(ref_tbl.strip().lower())

        # Step 2: Add scout-harvested operations (ALWAYS provisional per ONT-EVO-002)
        for cand in candidates:
            term = cand["term"]
            if self.pseudonymizer:
                term = self.pseudonymizer.de_pseudonymize(term)

            slug = _to_clean_slug(term)
            if not slug:
                continue

            evidence_refs = _clean_evidence_citations(
                cand.get("evidence_citations", []),
                self.pseudonymizer,
            )
            if cand["suggested_type"] == "entity":
                concept_uri = f"urn:ontology:provisional:entity:{slug}"
                canonical_concept_uri = f"urn:ontology:entity:{slug}"
                target_hub = f"hub_{slug}"
                dedup_key = ("add_concept", slug)
                if (
                    concept_uri not in existing_concepts
                    and canonical_concept_uri not in existing_concepts
                    and concept_uri not in explicit_concept_uris
                    and canonical_concept_uri not in explicit_concept_uris
                    and term.strip().lower() not in base_concept_names
                    and term.strip().lower() not in delta_concept_names
                    and target_hub.strip().lower() not in base_hubs
                    and target_hub.strip().lower() not in delta_hubs
                    and dedup_key not in seen_op_keys
                ):
                    seen_op_keys.add(dedup_key)
                    op_hash = hashlib.sha256(concept_uri.encode("utf-8")).hexdigest()[:8]
                    operations.append(
                        AddConceptOp(
                            op_id=f"op-concept-{op_hash}",
                            concept_uri=concept_uri,
                            concept_name=term,
                            target_hub=target_hub,
                            evidence_refs=evidence_refs,
                        )
                    )
                    explicit_concept_uris.add(concept_uri)
            elif cand["suggested_type"] == "enumeration":
                ref_name = f"ref_{slug}"
                enum_uri = f"urn:ontology:provisional:enum:{slug}"
                canonical_enum_uri = f"urn:ontology:enum:{slug}"
                dedup_key = ("add_reference_enumeration", slug)
                if (
                    enum_uri not in existing_enums
                    and canonical_enum_uri not in existing_enums
                    and enum_uri not in explicit_enum_uris
                    and canonical_enum_uri not in explicit_enum_uris
                    and ref_name.strip().lower() not in base_enum_names
                    and ref_name.strip().lower() not in delta_enum_names
                    and slug.strip().lower() not in base_enum_names
                    and slug.strip().lower() not in delta_enum_names
                    and dedup_key not in seen_op_keys
                ):
                    seen_op_keys.add(dedup_key)
                    op_hash = hashlib.sha256(enum_uri.encode("utf-8")).hexdigest()[:8]
                    # Deduplicate codes against any explicit AddReferenceEnumCodeOp (MAJ-01)
                    explicit_enum_codes = {
                        str(getattr(op, "code", ""))
                        for op in explicit_ops or []
                        if getattr(op, "op_type", None) == "add_reference_enum_code"
                        and (
                            getattr(op, "enum_uri", None) in (enum_uri, canonical_enum_uri)
                            or getattr(op, "enumeration_name", None) in (ref_name, slug)
                            or (
                                getattr(op, "enum_uri", "")
                                and getattr(op, "enum_uri", "").rsplit(":", 1)[-1] == slug
                            )
                            or (
                                getattr(op, "enumeration_name", "")
                                and _to_clean_slug(
                                    getattr(op, "enumeration_name", "")
                                ).removeprefix("ref_")
                                == slug
                            )
                        )
                    }
                    extracted_codes = [
                        c for c in cand.get("codes", []) if str(c) not in explicit_enum_codes
                    ]
                    operations.append(
                        AddReferenceEnumerationOp(
                            op_id=f"op-ref-{op_hash}",
                            enum_uri=enum_uri,
                            name=ref_name,
                            target_reference_table=ref_name,
                            allowed_codes=extracted_codes,
                            evidence_refs=evidence_refs,
                        )
                    )
                    explicit_enum_uris.add(enum_uri)

        # Step 3: Reconcile child ops to provisional parents or base ontology (MAJ-01)
        all_concept_uris = existing_concepts | explicit_concept_uris
        all_enum_uris = existing_enums | explicit_enum_uris
        all_enum_names = base_enum_names | delta_enum_names

        def _resolve_concept(uri: str) -> str | None:
            if not uri:
                return None
            if uri in all_concept_uris:
                return uri
            if uri in explicit_concept_uri_remapping:
                return explicit_concept_uri_remapping[uri]
            if uri.startswith("urn:ontology:entity:"):
                twin = uri.replace("urn:ontology:entity:", "urn:ontology:provisional:entity:")
                if twin in all_concept_uris:
                    return twin
            elif uri.startswith("urn:ontology:provisional:entity:"):
                twin = uri.replace("urn:ontology:provisional:entity:", "urn:ontology:entity:")
                if twin in all_concept_uris:
                    return twin
            return None

        def _resolve_enum(euri: str, ename: str) -> str | None:
            if euri:
                if euri in all_enum_uris:
                    return euri
                if euri in explicit_enum_uri_remapping:
                    return explicit_enum_uri_remapping[euri]
                if euri.startswith("urn:ontology:enum:"):
                    twin = euri.replace("urn:ontology:enum:", "urn:ontology:provisional:enum:")
                    if twin in all_enum_uris:
                        return twin
                elif euri.startswith("urn:ontology:provisional:enum:"):
                    twin = euri.replace("urn:ontology:provisional:enum:", "urn:ontology:enum:")
                    if twin in all_enum_uris:
                        return twin
                return None
            if ename:
                clean_ename = ename.strip().lower()
                matching_uris: list[str] = []
                for e in getattr(base_ontology, "reference_enumerations", None) or []:
                    e_name = (
                        (getattr(e, "name", None) or getattr(e, "enumeration_name", None) or "")
                        .strip()
                        .lower()
                    )
                    tbl = (getattr(e, "target_reference_table", None) or "").strip().lower()
                    if clean_ename in (e_name, tbl) and e.enum_uri:
                        matching_uris.append(e.enum_uri)
                for op in operations:
                    if getattr(op, "op_type", None) == "add_reference_enumeration":
                        op_name = (
                            (getattr(op, "name", "") or getattr(op, "target_reference_table", ""))
                            .strip()
                            .lower()
                        )
                        op_uri = getattr(op, "enum_uri", "")
                        if clean_ename == op_name and op_uri and op_uri not in matching_uris:
                            matching_uris.append(op_uri)
                if len(matching_uris) == 1:
                    return matching_uris[0]
                if len(matching_uris) == 0 and clean_ename in all_enum_names:
                    return clean_ename
            return None

        reconciled_operations: list[BaseOntologyDeltaOp] = []
        for op in operations:
            op_type = getattr(op, "op_type", "")
            if op_type in ("add_attribute", "add_external_mapping"):
                curi = getattr(op, "concept_uri", "")
                resolved = _resolve_concept(curi)
                if resolved:
                    reconciled_operations.append(
                        op
                        if resolved == curi
                        else type(op).model_validate(op.model_dump() | {"concept_uri": resolved})
                    )
                else:
                    raise DanglingParentReferenceError(
                        f"Child op '{getattr(op, 'op_id', 'unknown')}' missing parent '{curi}'."
                    )
            elif op_type == "add_relationship":
                src = getattr(op, "source_concept_uri", "")
                tgt = getattr(op, "target_concept_uri", "")
                res_src = _resolve_concept(src)
                res_tgt = _resolve_concept(tgt)
                if not res_src:
                    raise DanglingParentReferenceError(
                        f"Relationship '{getattr(op, 'op_id', 'unknown')}' source '{src}' missing."
                    )
                if not res_tgt:
                    raise DanglingParentReferenceError(
                        f"Relationship '{getattr(op, 'op_id', 'unknown')}' target '{tgt}' missing."
                    )
                updates: dict[str, Any] = {}
                if res_src != src:
                    updates["source_concept_uri"] = res_src
                if res_tgt != tgt:
                    updates["target_concept_uri"] = res_tgt
                reconciled_operations.append(
                    type(op).model_validate(op.model_dump() | updates) if updates else op
                )
            elif op_type == "add_reference_enum_code":
                euri = getattr(op, "enum_uri", "")
                ename = getattr(op, "enumeration_name", "")
                target_parent = _resolve_enum(euri, ename)
                if not target_parent:
                    raise DanglingParentReferenceError(
                        f"Child op '{getattr(op, 'op_id', 'unknown')}' missing '{euri or ename}'."
                    )
                if target_parent.startswith("urn:ontology:"):
                    target_slug = target_parent.rsplit(":", 1)[-1]
                    updates_code = {
                        "enum_uri": target_parent,
                        "enumeration_name": (ename if ename == target_slug else ""),
                    }
                else:
                    updates_code = {"enum_uri": "", "enumeration_name": target_parent}
                reconciled_operations.append(
                    type(op).model_validate(op.model_dump() | updates_code)
                    if (
                        updates_code.get("enum_uri") != euri
                        or updates_code.get("enumeration_name") != ename
                    )
                    else op
                )
            else:
                reconciled_operations.append(op)

        post_reconcile_ops: list[BaseOntologyDeltaOp] = []
        post_reconcile_keys: set[tuple[Any, ...]] = set()
        for op in reconciled_operations:
            k = _get_op_dedup_key(op)
            if k not in post_reconcile_keys:
                post_reconcile_keys.add(k)
                post_reconcile_ops.append(op)
        operations = post_reconcile_ops

        # Sort operations by dependency order so parents come before child ops (MAJ-01)
        op_dependency_order: dict[str, int] = {
            "add_concept": 0,
            "add_reference_enumeration": 0,
            "add_attribute": 1,
            "add_relationship": 2,
            "add_reference_enum_code": 3,
            "add_external_mapping": 4,
        }
        operations.sort(key=lambda op: op_dependency_order.get(getattr(op, "op_type", ""), 10))

        self._verify_additive_only(operations)

        # Security scans across all assembled operations (MIN-04)
        for op in operations:
            op_payload = op.model_dump_json()
            scan_redacted_placeholder_leak(op_payload)
            scan_unresolved_pseudonyms(op_payload)

        # Compute parent base ontology hash (ONT-EVO-003)
        if base_ontology is not None:
            base_sha = compute_ontology_sha256(base_ontology)
            base_ver = base_ontology.ontology_version
            if not base_ver:
                raise InvalidOntologyVersionError(
                    "Base ontology is missing required 'ontology_version'"
                )
            target_ver = _bump_minor_version(base_ver) if operations else base_ver
        else:
            base_ver = "0.0.0"
            base_sha = None
            target_ver = "1.0.0" if operations else "0.0.0"

        # Derived content hash from operations payload using canonical RFC 8785 JCS (MIN-03)
        op_digs = [
            hashlib.sha256(
                dumps_jcs(op.model_dump(mode="json", exclude_none=True)).encode("utf-8")
            ).hexdigest()
            for op in operations
        ]
        delta_content_hash = hashlib.sha256(":".join(op_digs).encode("utf-8")).hexdigest()[:16]
        delta_seed = f"{base_sha}:{target_ver}:{delta_content_hash}".encode()
        delta_id = f"delta-{hashlib.sha256(delta_seed).hexdigest()[:16]}"
        proposal_id = f"prop-{hashlib.sha256(delta_id.encode()).hexdigest()[:16]}"

        delta_author = (
            "agent:ontology-architect+caller" if explicit_ops else "agent:ontology-architect"
        )
        delta = OntologyDelta(
            delta_id=delta_id,
            base_ontology_version=base_ver,
            target_ontology_version=target_ver,
            parent_sha256=base_sha,
            author=delta_author,
            operations=operations,
            rationale="Agent-synthesized candidate ontology delta",
        )

        if not operations:
            confidence = 0.0
        else:
            grounded_count = sum(1 for op in operations if getattr(op, "evidence_refs", None))
            ratio = grounded_count / len(operations) if operations else 0.0
            confidence = round(0.70 + 0.20 * ratio, 2)

        return OntologyProposal(
            proposal_id=proposal_id,
            recommender_id="agent:ontology-architect",
            delta=delta,
            confidence_score=confidence,
            rationale="Agent-synthesized candidate ontology delta",
        )

    def clarify(
        self,
        context: Any,
        **kwargs: Any,
    ) -> list[ClarificationQuestion]:
        """Generate clarification questions for ambiguous context items (MAJ-04, MIN-09)."""
        candidate_terms = kwargs.get("candidate_terms")
        if not candidate_terms:
            if isinstance(context, OntologyProposal):
                candidate_terms = [
                    op.concept_name
                    for op in context.delta.operations
                    if isinstance(op, AddConceptOp)
                ]
            elif isinstance(context, OntologyDelta):
                candidate_terms = [
                    op.concept_name for op in context.operations if isinstance(op, AddConceptOp)
                ]
            elif isinstance(context, (list, tuple)) and context:
                # Handle heterogeneous items across list (MIN-08, MIN-09)
                candidate_terms = []
                chunks = [c for c in context if isinstance(c, EvidenceChunk)]
                if chunks:
                    candidate_terms.extend(
                        self.term_scout.extract_candidates(evidence_chunks=chunks)
                    )
                for item in context:
                    if isinstance(item, str) or (isinstance(item, dict) and "term" in item):
                        candidate_terms.append(item)
            elif isinstance(context, dict) and "terms" in context:
                candidate_terms = list(context["terms"])
            elif isinstance(context, str):
                candidate_terms = [context]
            else:
                candidate_terms = []

        max_questions = kwargs.get("max_questions", 5)
        questions = self.domain_clarifier.generate_questions_for_terms(
            candidate_terms=candidate_terms or [],
            max_questions=max_questions,
        )

        # Scan evidence citations for leaks and pseudonyms before returning (MIN-01, MIN-05)
        clean_questions: list[ClarificationQuestion] = []
        for q in questions:
            clean_citations = _clean_evidence_citations(
                q.evidence_citations,
                self.pseudonymizer,
            )
            clean_questions.append(q.model_copy(update={"evidence_citations": clean_citations}))
        return clean_questions

    def _verify_additive_only(self, operations: Sequence[BaseOntologyDeltaOp]) -> None:
        """Validate that proposed delta operations contain strictly allowed additive ops."""
        for op in operations:
            op_type = getattr(op, "op_type", "")
            if op_type not in _ALLOWED_ADDITIVE_OP_TYPES:
                raise AgentDestructiveOpForbiddenError(
                    f"Automated agent proposals must be strictly additive; "
                    f"operation '{op_type}' is forbidden."
                )
