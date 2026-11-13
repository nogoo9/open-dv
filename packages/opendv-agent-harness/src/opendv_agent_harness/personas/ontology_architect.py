"""`ontology-architect` Persona Builder: Deep Reasoning Construction Agent SPI."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
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
    validate_task_spec_privacy,
)
from opendv_agent_harness.routing import get_persona_capability
from opendv_agent_harness.tools.catalog import get_tools_for_persona
from opendv_contracts.dtos.clarification import ClarificationQuestion
from opendv_contracts.dtos.ingestion import EvidenceChunk, TableStructure
from opendv_contracts.dtos.ontology import (
    ALLOWED_PATH_A_PREDICATES,
    EXISTING_CONCEPT_MUTATING_OP_TYPES,
    PATH_A_GROUNDING_THRESHOLD,
    SEMVER_RANKS,
    AddConceptOp,
    AddExternalMappingOp,
    AddReferenceEnumerationOp,
    BaseOntologyDeltaOp,
    EvidenceRef,
    OntologyDelta,
    OntologyProposal,
    StewardWaiverRecord,
)
from opendv_contracts.jcs import dumps_jcs
from opendv_contracts.spi.agent import ConstructionAgent
from opendv_contracts.spi.grounding import GroundingVerifier
from opendv_core.models.graph_a import ExternalMapping, GraphAOntology
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
        "add_synonym",
        "set_parent_concept",
        "set_concept_elm_archetype",
        "add_key_authority_pattern",
        "add_governance_constraint",
        "rescore_external_mapping",
        "repin_external_mapping",
        "revise_description",
        "refine_definition",
        "annotate_concept_metadata",
        "add_source_hint",
    }
)

_CONCEPT_CHILD_OP_TYPES: frozenset[str] = (
    EXISTING_CONCEPT_MUTATING_OP_TYPES & _ALLOWED_ADDITIVE_OP_TYPES
)

_NORMALIZED_PATH_A_PREDICATES: frozenset[str] = frozenset(
    {p.lower() for p in ALLOWED_PATH_A_PREDICATES}
    | {
        "exactmatch",
        "closematch",
        "https://www.w3.org/2004/02/skos/core#exactmatch",
        "https://www.w3.org/2004/02/skos/core#closematch",
    }
)


class InvalidOntologyVersionError(ValueError):
    """Raised when base ontology version is not a valid SemVer string."""

    exit_code = 4
    http_status = 422
    error_code = "INVALID_ONTOLOGY_VERSION"


def _parse_semver_tuple(ver: str) -> tuple[int, int, int]:
    parts = ver.strip().split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise InvalidOntologyVersionError(
            f"Invalid SemVer base version '{ver}', expected 'MAJOR.MINOR.PATCH'"
        )
    return int(parts[0]), int(parts[1]), int(parts[2])


def _compute_target_version(
    base_ver: str,
    operations: Sequence[BaseOntologyDeltaOp],
    base_ontology: GraphAOntology | None = None,
) -> str:
    """Compute target SemVer version consistent with ONT-EVO-001 SemVer calculus."""
    clean_base = base_ver.strip() if base_ver else "0.0.0"
    b_maj, b_min, b_pat = _parse_semver_tuple(clean_base)
    if not operations:
        return clean_base

    max_rank = max(
        SEMVER_RANKS.get(op.effective_semver_class(base_ontology), 1) for op in operations
    )
    req = "MAJOR" if max_rank == 3 else ("MINOR" if max_rank == 2 else "PATCH")

    if b_maj == 0:
        base_has_cbcs = bool(
            base_ontology and getattr(base_ontology, "canonical_business_concepts", None)
        )
        adds_cbc = any(getattr(op, "op_type", "") == "add_concept" for op in operations)
        if not base_has_cbcs and adds_cbc:
            return "1.0.0"
        return f"0.{b_min + 1}.0"

    if req == "MAJOR":
        return f"{b_maj + 1}.0.0"
    if req == "MINOR":
        return f"{b_maj}.{b_min + 1}.0"
    return f"{b_maj}.{b_min}.{b_pat + 1}"


def _normalize_path_a_predicate(predicate: str | None) -> str | None:
    """Normalize supported SKOS exact/close match variants to canonical skos:* token."""
    if not predicate:
        return None
    p = predicate.strip().lower()
    if p in ("skos:exactmatch", "exactmatch", "https://www.w3.org/2004/02/skos/core#exactmatch"):
        return "skos:exactMatch"
    if p in ("skos:closematch", "closematch", "https://www.w3.org/2004/02/skos/core#closematch"):
        return "skos:closeMatch"
    return None


def _is_fibo_path_a_mapping(
    scheme: str | None,
    target_uri: str | None,
    predicate: str | None,
    score: float | None,
) -> bool:
    """Return True iff mapping satisfies ONT-EVO-002 Path A FIBO grounding criteria."""
    if _normalize_path_a_predicate(predicate) is None:
        return False
    tgt = (target_uri or "").strip()
    if not tgt:
        return False
    if (scheme or "").strip().upper() != "FIBO":
        return False
    if score is None:
        return False
    return float(score) >= PATH_A_GROUNDING_THRESHOLD


def _verify_waiver_record(
    waiver: StewardWaiverRecord,
    *,
    waiver_secret: bytes | str | None,
    current_base_sha256: str,
) -> bool:
    """Verify cryptographic K_waiver HMAC, role, rationale, expiry, and base SHA-256."""
    if isinstance(waiver_secret, bytes):
        key_bytes = waiver_secret
    elif isinstance(waiver_secret, str):
        key_bytes = waiver_secret.encode("utf-8")
    else:
        return False
    if not key_bytes:
        return False
    if getattr(waiver, "authorized_role", None) != "admin":
        return False
    if len((getattr(waiver, "rationale", "") or "").strip()) < 10:
        return False
    exp = getattr(waiver, "expires_at", None)
    if exp is not None:
        exp_utc = exp if exp.tzinfo is not None else exp.replace(tzinfo=UTC)
        if exp_utc <= datetime.now(UTC):
            return False
    w_base_sha = (getattr(waiver, "base_sha256", "") or "").strip().lower()
    if w_base_sha and current_base_sha256 and w_base_sha != current_base_sha256.lower():
        return False
    signing_fn = getattr(waiver, "signing_payload", None)
    if not callable(signing_fn):
        return False
    raw_sig = (getattr(waiver, "signature_or_token", "") or "").strip().lower()
    if raw_sig.startswith("hmac-sha256:"):
        raw_sig = raw_sig.removeprefix("hmac-sha256:").strip()
    if not raw_sig:
        return False
    expected_sig = (
        hmac.new(
            key_bytes,
            signing_fn().encode("utf-8"),
            hashlib.sha256,
        )
        .hexdigest()
        .lower()
    )
    return hmac.compare_digest(raw_sig, expected_sig)


def _coerce_fibo_external_mapping(em: Any) -> ExternalMapping | None:
    """Convert raw mapping dict/model/AddExternalMappingOp into a Path A ExternalMapping."""
    if isinstance(em, dict):
        em_scheme = em.get("scheme") or em.get("standard_name")
        em_tgt = em.get("target_uri") or em.get("standard_uri")
        em_pred = em.get("predicate") or em.get("mapping_type")
        em_conf = (
            em.get("mapping_confidence")
            if em.get("mapping_confidence") is not None
            else em.get("confidence")
        )
    elif getattr(em, "op_type", None) == "add_external_mapping":
        em_scheme = getattr(em, "scheme", None)
        em_tgt = getattr(em, "target_uri", None)
        em_pred = getattr(em, "predicate", None)
        em_conf = _extract_mapping_op_score(em)
    else:
        em_scheme = getattr(em, "scheme", None) or getattr(em, "standard_name", None)
        em_tgt = getattr(em, "target_uri", None) or getattr(em, "standard_uri", None)
        em_pred = getattr(em, "predicate", None) or getattr(em, "mapping_type", None)
        em_conf = (
            getattr(em, "mapping_confidence", None)
            if getattr(em, "mapping_confidence", None) is not None
            else getattr(em, "confidence", None)
        )
    norm_pred = _normalize_path_a_predicate(em_pred)
    if not _is_fibo_path_a_mapping(em_scheme, em_tgt, norm_pred, em_conf):
        return None
    assert norm_pred is not None and em_tgt is not None and em_conf is not None
    return ExternalMapping(
        scheme="FIBO",
        target_uri=str(em_tgt).strip(),
        predicate=norm_pred,
        confidence=float(em_conf),
    )


def _verify_concept_fibo_grounding(
    concept_uri: str,
    concept_name: str,
    synonyms: Sequence[str],
    candidates: Sequence[ExternalMapping],
    verifier: GroundingVerifier | None,
) -> bool:
    """Verify Path A FIBO mappings structurally and via GroundingVerifier if provided."""
    fibo_candidates = [
        m
        for m in candidates
        if _is_fibo_path_a_mapping(m.scheme, m.target_uri, m.predicate, m.confidence)
    ]
    if not fibo_candidates:
        return False
    if verifier is None:
        return True
    res = verifier.verify_grounding(
        concept_uri,
        candidates=fibo_candidates,
        concept_name=concept_name,
        synonyms=tuple(synonyms),
    )
    return bool(res.is_grounded and res.score >= PATH_A_GROUNDING_THRESHOLD)


def _extract_mapping_op_score(op: BaseOntologyDeltaOp) -> float | None:
    """Extract mapping confidence score from an AddExternalMappingOp without trusting defaults."""
    mc = getattr(op, "mapping_confidence", None)
    if mc is not None:
        return float(mc)
    fields_set = getattr(op, "model_fields_set", set())
    if "confidence" in fields_set and getattr(op, "confidence", None) is not None:
        return float(op.confidence)
    return None


def _canonical_entity_uri(slug: str) -> str:
    return f"urn:ontology:entity:{slug}"


def _provisional_entity_uri(slug: str) -> str:
    return f"urn:opendv:provisional:entity:{slug}"


def _canonical_enum_uri(slug: str) -> str:
    return f"urn:ontology:enum:{slug}"


def _provisional_enum_uri(slug: str) -> str:
    return f"urn:opendv:provisional:enum:{slug}"


def _canonical_rel_uri(raw_uri: str, slug: str) -> str:
    kind = "event" if ":event:" in raw_uri else "relationship"
    return f"urn:ontology:{kind}:{slug}"


def _provisional_rel_uri(raw_uri: str, slug: str) -> str:
    kind = "event" if ":event:" in raw_uri else "relationship"
    return f"urn:opendv:provisional:{kind}:{slug}"


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
    validate_task_spec_privacy(
        prompt=prompt,
        system_instructions=system_instructions,
        input_data=input_data,
    )
    profile = get_persona_capability(AgentPersona.ONTOLOGY_ARCHITECT)
    eff_trace = trace_id or uuid.uuid4().hex
    if tools is not None:
        for t in tools:
            if (
                t.name not in profile.authorized_tools
                or AgentPersona.ONTOLOGY_ARCHITECT not in t.allowed_personas
            ):
                raise PermissionError(
                    f"Tool '{t.name}' is not authorized for persona "
                    f"'{AgentPersona.ONTOLOGY_ARCHITECT.value}'."
                )
        eff_tools = tuple(tools)
    else:
        eff_tools = tuple(get_tools_for_persona(AgentPersona.ONTOLOGY_ARCHITECT))
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
        matcher: Any = None,
        waiver_secret: bytes | str | None = None,
        grounding_verifier: GroundingVerifier | None = None,
    ) -> None:
        self.pseudonymizer = pseudonymizer
        self.term_scout = term_scout or TermScout(pseudonymizer=pseudonymizer)
        self.domain_clarifier = domain_clarifier or DomainClarifier(pseudonymizer=pseudonymizer)
        self.matcher = matcher
        self.waiver_secret = waiver_secret
        self.grounding_verifier = grounding_verifier

    def propose_delta(
        self,
        base_ontology: Any,
        evidence: list[Any] | None = None,
        **kwargs: Any,
    ) -> OntologyProposal:
        """Synthesize additive ontology delta proposal implementing ConstructionAgent SPI."""
        if base_ontology is not None and not isinstance(base_ontology, GraphAOntology):
            raise TypeError(
                f"base_ontology must be GraphAOntology or None, got: {type(base_ontology)}"
            )

        explicit_ops: list[BaseOntologyDeltaOp] | None = kwargs.get("operations")
        if explicit_ops is not None:
            for op in explicit_ops:
                if not isinstance(op, BaseOntologyDeltaOp):
                    raise TypeError(
                        f"All operations in kwargs must be BaseOntologyDeltaOp, got: {type(op)}"
                    )
            self._verify_additive_only(explicit_ops, base_ontology=base_ontology)

        waivers: Sequence[StewardWaiverRecord] = kwargs.get("waivers") or ()
        active_matcher = kwargs.get("matcher", self.matcher)
        active_waiver_secret: bytes | str | None = kwargs.get("waiver_secret", self.waiver_secret)
        active_verifier: GroundingVerifier | None = kwargs.get(
            "grounding_verifier", self.grounding_verifier
        )

        chunks: list[EvidenceChunk] = []
        tables: list[TableStructure] = []
        if evidence:
            for item in evidence:
                if isinstance(item, EvidenceChunk):
                    chunks.append(item)
                elif isinstance(item, TableStructure):
                    tables.append(item)

        candidates = self.term_scout.extract_candidates(chunks, tables)

        existing_concepts: set[str] = set()
        base_concept_names: set[str] = set()
        base_hubs: set[str] = set()
        existing_enums: set[str] = set()
        base_enum_names: set[str] = set()
        base_enum_slugs: set[str] = set()
        base_enum_slug_to_uris: dict[str, list[str]] = {}

        def _record_base_enum_slug(slug_val: str, enum_uri_val: str) -> None:
            if not slug_val or not enum_uri_val:
                return
            base_enum_slugs.add(slug_val)
            bucket = base_enum_slug_to_uris.setdefault(slug_val, [])
            if enum_uri_val not in bucket:
                bucket.append(enum_uri_val)

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
                    _record_base_enum_slug(e.enum_uri.rsplit(":", 1)[-1], e.enum_uri)
                if e.target_reference_table:
                    tbl_l = e.target_reference_table.strip().lower()
                    base_enum_names.add(tbl_l)
                    if e.enum_uri:
                        _record_base_enum_slug(
                            _to_clean_slug(tbl_l).removeprefix("ref_"), e.enum_uri
                        )
                    else:
                        base_enum_slugs.add(_to_clean_slug(tbl_l).removeprefix("ref_"))
                ename = getattr(e, "name", None) or getattr(e, "enumeration_name", None)
                if ename:
                    en_l = ename.strip().lower()
                    base_enum_names.add(en_l)
                    if e.enum_uri:
                        _record_base_enum_slug(
                            _to_clean_slug(en_l).removeprefix("ref_"), e.enum_uri
                        )
                    else:
                        base_enum_slugs.add(_to_clean_slug(en_l).removeprefix("ref_"))

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
            if op_type == "add_relationship":
                src = getattr(op, "source_concept_uri", "")
                rel_ident = (
                    getattr(op, "relationship_uri", "")
                    or getattr(op, "relationship_name", "")
                    or getattr(op, "target_link", "")
                    or op_id
                )
                tgt = getattr(op, "target_concept_uri", "")
                return ("add_relationship", src, rel_ident, tgt)
            if op_type == "add_reference_enumeration":
                euri = getattr(op, "enum_uri", "")
                ename = (
                    getattr(op, "name", "") or getattr(op, "target_reference_table", "") or op_id
                )
                slug = (
                    euri.rsplit(":", 1)[-1] if euri else _to_clean_slug(ename).removeprefix("ref_")
                )
                return ("add_reference_enumeration", slug or op_id)
            if op_type == "add_attribute":
                curi = getattr(op, "concept_uri", "") or op_id
                aname = getattr(op, "attribute_name", "") or op_id
                return ("add_attribute", curi, aname)
            if op_type == "add_reference_enum_code":
                euri = getattr(op, "enum_uri", "")
                ename = getattr(op, "enumeration_name", "")
                parent_ident = (
                    (euri.rsplit(":", 1)[-1] if euri else "")
                    or _to_clean_slug(ename).removeprefix("ref_")
                    or op_id
                )
                code = str(getattr(op, "code", "")) or op_id
                return ("add_reference_enum_code", parent_ident, code)
            if op_type == "add_external_mapping":
                curi = getattr(op, "concept_uri", "") or op_id
                std = getattr(op, "scheme", "") or getattr(op, "standard", "") or ""
                tgt = getattr(op, "target_uri", "") or op_id
                pred = getattr(op, "predicate", "") or ""
                return ("add_external_mapping", curi, std, tgt, pred)
            if op_type == "add_synonym":
                return ("add_synonym", getattr(op, "concept_uri", ""), getattr(op, "synonym", ""))
            return (op_type, op_id)

        candidate_concept_slugs: set[str] = set()
        concept_uri_to_canon: dict[str, str] = {}
        candidate_enum_slugs: set[str] = set()
        enum_uri_to_canon: dict[str, str] = {}
        delta_concept_names: set[str] = set()
        delta_hubs: set[str] = set()
        delta_enum_names: set[str] = set()

        # Step 1: Add caller-supplied explicit operations first
        if explicit_ops:
            for ex_op in explicit_ops:
                op_type = getattr(ex_op, "op_type", None)
                if op_type == "add_concept":
                    raw_curi = getattr(ex_op, "concept_uri", "")
                    cname = getattr(ex_op, "concept_name", "")
                    slug = (
                        raw_curi.rsplit(":", 1)[-1]
                        if raw_curi
                        else (_to_clean_slug(cname) or "concept")
                    )
                    canon_uri = _canonical_entity_uri(slug)
                    legacy_prov_uri = f"urn:ontology:provisional:entity:{slug}"
                    opendv_prov_uri = _provisional_entity_uri(slug)

                    is_base = (
                        raw_curi in existing_concepts
                        or canon_uri in existing_concepts
                        or legacy_prov_uri in existing_concepts
                        or opendv_prov_uri in existing_concepts
                    )
                    if is_base:
                        base_uri = (
                            canon_uri
                            if canon_uri in existing_concepts
                            else (
                                raw_curi
                                if raw_curi in existing_concepts
                                else next(
                                    u
                                    for u in (legacy_prov_uri, opendv_prov_uri)
                                    if u in existing_concepts
                                )
                            )
                        )
                        for alias_u in {raw_curi, canon_uri, legacy_prov_uri, opendv_prov_uri}:
                            if alias_u:
                                concept_uri_to_canon[alias_u] = base_uri
                        continue

                    if slug in candidate_concept_slugs:
                        continue

                    for alias_u in {raw_curi, canon_uri, legacy_prov_uri, opendv_prov_uri}:
                        if alias_u:
                            concept_uri_to_canon[alias_u] = canon_uri
                    if raw_curi != canon_uri:
                        ex_op = type(ex_op).model_validate(
                            ex_op.model_dump() | {"concept_uri": canon_uri}
                        )

                elif op_type == "add_reference_enumeration":
                    raw_euri = getattr(ex_op, "enum_uri", "")
                    ename = getattr(ex_op, "name", "") or getattr(
                        ex_op, "target_reference_table", ""
                    )
                    slug = (
                        raw_euri.rsplit(":", 1)[-1]
                        if raw_euri
                        else (_to_clean_slug(ename).removeprefix("ref_") or "enum")
                    )
                    canon_euri = _canonical_enum_uri(slug)
                    legacy_prov_euri = f"urn:ontology:provisional:enum:{slug}"
                    opendv_prov_euri = _provisional_enum_uri(slug)

                    is_base_enum = (
                        raw_euri in existing_enums
                        or canon_euri in existing_enums
                        or slug in base_enum_slugs
                    )
                    if is_base_enum:
                        if raw_euri in existing_enums:
                            base_euri = raw_euri
                        elif canon_euri in existing_enums:
                            base_euri = canon_euri
                        else:
                            matched_base_uris = base_enum_slug_to_uris.get(slug, [])
                            if len(matched_base_uris) != 1:
                                raise DanglingParentReferenceError(
                                    f"Ambiguous or unresolved base enumeration slug '{slug}' "
                                    f"for explicit enumeration operation '{raw_euri or ename}'."
                                )
                            base_euri = matched_base_uris[0]
                        for alias_u in {raw_euri, canon_euri, legacy_prov_euri, opendv_prov_euri}:
                            if alias_u:
                                enum_uri_to_canon[alias_u] = base_euri
                        continue

                    if slug in candidate_enum_slugs:
                        continue

                    for alias_u in {raw_euri, canon_euri, legacy_prov_euri, opendv_prov_euri}:
                        if alias_u:
                            enum_uri_to_canon[alias_u] = canon_euri
                    if raw_euri != canon_euri:
                        ex_op = type(ex_op).model_validate(
                            ex_op.model_dump() | {"enum_uri": canon_euri}
                        )

                elif op_type == "add_relationship":
                    raw_ruri = getattr(ex_op, "relationship_uri", "")
                    rname = getattr(ex_op, "relationship_name", "") or getattr(
                        ex_op, "target_link", ""
                    )
                    rel_slug = (
                        raw_ruri.rsplit(":", 1)[-1]
                        if raw_ruri
                        else (_to_clean_slug(rname).removeprefix("lnk_") or "rel")
                    )
                    canon_ruri = _canonical_rel_uri(raw_ruri, rel_slug)
                    if raw_ruri != canon_ruri:
                        ex_op = type(ex_op).model_validate(
                            ex_op.model_dump() | {"relationship_uri": canon_ruri}
                        )

                elif op_type == "add_external_mapping":
                    mc = _extract_mapping_op_score(ex_op)
                    if getattr(ex_op, "mapping_confidence", None) is None and mc is not None:
                        ex_op = type(ex_op).model_validate(
                            ex_op.model_dump() | {"mapping_confidence": mc}
                        )

                key = _get_op_dedup_key(ex_op)
                if key not in seen_op_keys:
                    seen_op_keys.add(key)
                    operations.append(ex_op)
                    if op_type == "add_concept":
                        c_slug = getattr(ex_op, "concept_uri", "").rsplit(":", 1)[-1]
                        if c_slug:
                            candidate_concept_slugs.add(c_slug)
                        c_name = getattr(ex_op, "concept_name", "")
                        if c_name:
                            delta_concept_names.add(c_name.strip().lower())
                        hub = getattr(ex_op, "target_hub", "")
                        if hub:
                            delta_hubs.add(hub.strip().lower())
                    elif op_type == "add_reference_enumeration":
                        e_slug = getattr(ex_op, "enum_uri", "").rsplit(":", 1)[-1]
                        if e_slug:
                            candidate_enum_slugs.add(e_slug)
                            delta_enum_names.add(e_slug.lower())
                            delta_enum_names.add(f"ref_{e_slug}".lower())
                        ename = getattr(ex_op, "name", "")
                        if ename:
                            delta_enum_names.add(ename.strip().lower())
                        ref_tbl = getattr(ex_op, "target_reference_table", "")
                        if ref_tbl:
                            delta_enum_names.add(ref_tbl.strip().lower())

        # Step 2: Add scout-harvested operations
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
                canon_uri = _canonical_entity_uri(slug)
                target_hub = f"hub_{slug}"
                dedup_key = ("add_concept", slug)
                if (
                    canon_uri not in existing_concepts
                    and slug not in candidate_concept_slugs
                    and term.strip().lower() not in base_concept_names
                    and term.strip().lower() not in delta_concept_names
                    and target_hub.strip().lower() not in base_hubs
                    and target_hub.strip().lower() not in delta_hubs
                    and dedup_key not in seen_op_keys
                ):
                    seen_op_keys.add(dedup_key)
                    op_hash = hashlib.sha256(canon_uri.encode("utf-8")).hexdigest()[:8]
                    operations.append(
                        AddConceptOp(
                            op_id=f"op-concept-{op_hash}",
                            concept_uri=canon_uri,
                            concept_name=term,
                            target_hub=target_hub,
                            evidence_refs=evidence_refs,
                        )
                    )
                    candidate_concept_slugs.add(slug)
                    concept_uri_to_canon[canon_uri] = canon_uri
                    concept_uri_to_canon[f"urn:ontology:provisional:entity:{slug}"] = canon_uri
                    concept_uri_to_canon[_provisional_entity_uri(slug)] = canon_uri

                    # Optional injected ConceptMatcher grounding support
                    if active_matcher is not None and hasattr(active_matcher, "match"):
                        matches = active_matcher.match(term, top_k=1)
                        if matches:
                            top_m = matches[0]
                            m_score = float(getattr(top_m, "normalized_score", 0.0))
                            m_uri = str(getattr(top_m, "concept_uri", ""))
                            if m_uri and m_score >= PATH_A_GROUNDING_THRESHOLD:
                                pred = "skos:exactMatch" if m_score >= 0.85 else "skos:closeMatch"
                                map_op = AddExternalMappingOp(
                                    op_id=f"op-map-{op_hash}",
                                    concept_uri=canon_uri,
                                    scheme="FIBO",
                                    target_uri=m_uri,
                                    predicate=pred,
                                    confidence=m_score,
                                    mapping_confidence=m_score,
                                    evidence_refs=evidence_refs,
                                )
                                m_key = _get_op_dedup_key(map_op)
                                if m_key not in seen_op_keys:
                                    seen_op_keys.add(m_key)
                                    operations.append(map_op)

            elif cand["suggested_type"] == "enumeration":
                ref_name = f"ref_{slug}"
                canon_euri = _canonical_enum_uri(slug)
                dedup_key = ("add_reference_enumeration", slug)
                if (
                    canon_euri not in existing_enums
                    and slug not in base_enum_slugs
                    and slug not in candidate_enum_slugs
                    and ref_name.strip().lower() not in base_enum_names
                    and ref_name.strip().lower() not in delta_enum_names
                    and slug.strip().lower() not in base_enum_names
                    and slug.strip().lower() not in delta_enum_names
                    and dedup_key not in seen_op_keys
                ):
                    seen_op_keys.add(dedup_key)
                    op_hash = hashlib.sha256(canon_euri.encode("utf-8")).hexdigest()[:8]
                    explicit_enum_codes = {
                        str(getattr(op, "code", ""))
                        for op in explicit_ops or []
                        if getattr(op, "op_type", None) == "add_reference_enum_code"
                        and (
                            (
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
                            enum_uri=canon_euri,
                            name=ref_name,
                            target_reference_table=ref_name,
                            allowed_codes=extracted_codes,
                            evidence_refs=evidence_refs,
                        )
                    )
                    candidate_enum_slugs.add(slug)
                    delta_enum_names.add(ref_name.lower())
                    delta_enum_names.add(slug.lower())
                    enum_uri_to_canon[canon_euri] = canon_euri
                    enum_uri_to_canon[f"urn:ontology:provisional:enum:{slug}"] = canon_euri
                    enum_uri_to_canon[_provisional_enum_uri(slug)] = canon_euri

        # Step 3: Reconcile child ops & validate parent references (P09::MAJ-02)
        all_concept_uris = existing_concepts | {
            _canonical_entity_uri(s) for s in candidate_concept_slugs
        }
        all_enum_uris = existing_enums | {_canonical_enum_uri(s) for s in candidate_enum_slugs}
        all_enum_names = base_enum_names | delta_enum_names

        def _resolve_concept(uri: str) -> str | None:
            if not uri:
                return None
            if uri in all_concept_uris:
                return uri
            if uri in concept_uri_to_canon:
                return concept_uri_to_canon[uri]
            slug = uri.rsplit(":", 1)[-1]
            canon = _canonical_entity_uri(slug)
            if canon in all_concept_uris:
                return canon
            return None

        def _resolve_enum(euri: str, ename: str) -> str | None:
            if euri:
                if euri in all_enum_uris:
                    return euri
                mapped = enum_uri_to_canon.get(euri)
                if mapped and mapped in all_enum_uris:
                    return mapped
                slug = euri.rsplit(":", 1)[-1]
                canon = _canonical_enum_uri(slug)
                if canon in all_enum_uris:
                    return canon
                matched_base = base_enum_slug_to_uris.get(slug, [])
                if len(matched_base) == 1 and matched_base[0] in all_enum_uris:
                    return matched_base[0]
                return None
            if ename:
                clean_ename = ename.strip().lower()
                norm_slug = _to_clean_slug(clean_ename).removeprefix("ref_")
                matching_uris: list[str] = []
                for e in getattr(base_ontology, "reference_enumerations", None) or []:
                    e_name = (
                        (getattr(e, "name", None) or getattr(e, "enumeration_name", None) or "")
                        .strip()
                        .lower()
                    )
                    tbl = (getattr(e, "target_reference_table", None) or "").strip().lower()
                    e_slug = e.enum_uri.rsplit(":", 1)[-1] if e.enum_uri else ""
                    if (
                        clean_ename in (e_name, tbl)
                        or (
                            norm_slug
                            and norm_slug
                            in (
                                _to_clean_slug(e_name).removeprefix("ref_"),
                                _to_clean_slug(tbl).removeprefix("ref_"),
                                e_slug,
                            )
                        )
                    ) and e.enum_uri:
                        matching_uris.append(e.enum_uri)
                for op in operations:
                    if getattr(op, "op_type", None) == "add_reference_enumeration":
                        op_name = (getattr(op, "name", "") or "").strip().lower()
                        op_tbl = (getattr(op, "target_reference_table", "") or "").strip().lower()
                        op_uri = getattr(op, "enum_uri", "")
                        op_slug = op_uri.rsplit(":", 1)[-1] if op_uri else ""
                        if (
                            (
                                clean_ename in (op_name, op_tbl)
                                or (
                                    norm_slug
                                    and norm_slug
                                    in (
                                        _to_clean_slug(op_name).removeprefix("ref_"),
                                        _to_clean_slug(op_tbl).removeprefix("ref_"),
                                        op_slug,
                                    )
                                )
                            )
                            and op_uri
                            and op_uri not in matching_uris
                        ):
                            matching_uris.append(op_uri)
                if len(matching_uris) == 1:
                    return matching_uris[0]
                if len(matching_uris) == 0 and clean_ename in all_enum_names:
                    return clean_ename
            return None

        reconciled_operations: list[BaseOntologyDeltaOp] = []
        for op in operations:
            op_type = getattr(op, "op_type", "")
            if op_type in _CONCEPT_CHILD_OP_TYPES:
                curi = getattr(op, "concept_uri", "")
                resolved = _resolve_concept(curi)
                if not resolved:
                    raise DanglingParentReferenceError(
                        f"Child op '{getattr(op, 'op_id', 'unknown')}' missing parent '{curi}'."
                    )
                updates_c: dict[str, Any] = {}
                if resolved != curi:
                    updates_c["concept_uri"] = resolved
                if op_type == "set_parent_concept":
                    p_uri = getattr(op, "parent_concept_uri", None)
                    if p_uri:
                        res_parent = _resolve_concept(p_uri)
                        if not res_parent:
                            raise DanglingParentReferenceError(
                                f"SetParentConceptOp '{getattr(op, 'op_id', 'unknown')}' "
                                f"missing parent_concept_uri '{p_uri}'."
                            )
                        if res_parent != p_uri:
                            updates_c["parent_concept_uri"] = res_parent
                reconciled_operations.append(
                    type(op).model_validate(op.model_dump() | updates_c) if updates_c else op
                )
            elif op_type == "add_relationship":
                raw_eps = list(getattr(op, "endpoints", None) or [])
                src = getattr(op, "source_concept_uri", "") or (raw_eps[0] if raw_eps else "")
                tgt = getattr(op, "target_concept_uri", "") or (
                    raw_eps[1] if len(raw_eps) > 1 else ""
                )
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
                updates_r: dict[str, Any] = {}
                if res_src != src:
                    updates_r["source_concept_uri"] = res_src
                if res_tgt != tgt:
                    updates_r["target_concept_uri"] = res_tgt
                if raw_eps:
                    resolved_eps: list[str] = []
                    for ep in raw_eps:
                        r_ep = _resolve_concept(ep)
                        if not r_ep:
                            raise DanglingParentReferenceError(
                                f"Relationship '{getattr(op, 'op_id', 'unknown')}' "
                                f"endpoint '{ep}' missing."
                            )
                        resolved_eps.append(r_ep)
                    updates_r["endpoints"] = resolved_eps
                reconciled_operations.append(
                    type(op).model_validate(op.model_dump() | updates_r) if updates_r else op
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

        # Step 4: ONT-EVO-002 Path A / Path B Grounding Evaluation & Staging Segregation
        # (Fixes P09::MAJ-01, P09::MAJ-02, and CRIT-06)
        current_base_sha256 = (
            compute_ontology_sha256(base_ontology)
            if isinstance(base_ontology, GraphAOntology)
            else ""
        )
        waived_concept_uris: set[str] = set()
        for w in waivers:
            if _verify_waiver_record(
                w,
                waiver_secret=active_waiver_secret,
                current_base_sha256=current_base_sha256,
            ):
                for t_uri in getattr(w, "target_concept_uris", ()):
                    if isinstance(t_uri, str) and t_uri.strip():
                        waived_concept_uris.add(t_uri.strip())

        paired_mapping_ops_by_concept: dict[str, list[BaseOntologyDeltaOp]] = {}
        rescore_ops_by_concept: dict[str, list[BaseOntologyDeltaOp]] = {}
        added_synonyms_by_concept: dict[str, list[str]] = {}
        enum_code_counts: dict[str, int] = {}
        for op in post_reconcile_ops:
            ot = getattr(op, "op_type", "")
            if ot == "add_external_mapping":
                paired_mapping_ops_by_concept.setdefault(getattr(op, "concept_uri", ""), []).append(
                    op
                )
            elif ot == "rescore_external_mapping":
                rescore_ops_by_concept.setdefault(getattr(op, "concept_uri", ""), []).append(op)
            elif ot == "add_synonym":
                syn = getattr(op, "synonym", "")
                if syn:
                    added_synonyms_by_concept.setdefault(getattr(op, "concept_uri", ""), []).append(
                        str(syn)
                    )
            elif ot == "add_reference_enum_code":
                eu = getattr(op, "enum_uri", "")
                if eu:
                    enum_code_counts[eu] = enum_code_counts.get(eu, 0) + 1

        def _apply_rescores(
            raw_mappings: list[ExternalMapping], curi: str
        ) -> list[ExternalMapping]:
            rescores = rescore_ops_by_concept.get(curi, ())
            if not rescores:
                return raw_mappings
            updated: list[ExternalMapping] = []
            for m in raw_mappings:
                conf = m.confidence
                for rop in rescores:
                    r_scheme = (getattr(rop, "scheme", "") or "").strip().upper()
                    r_tgt = (getattr(rop, "target_uri", "") or "").strip()
                    if r_tgt == m.target_uri.strip() and (
                        not r_scheme or r_scheme == (m.scheme or "").strip().upper()
                    ):
                        conf = float(getattr(rop, "new_confidence", conf or 0.0))
                if conf is not None and conf >= PATH_A_GROUNDING_THRESHOLD:
                    updated.append(m.model_copy(update={"confidence": conf}))
            return updated

        base_concepts_by_uri: dict[str, Any] = {}
        if isinstance(base_ontology, GraphAOntology):
            for bc in getattr(base_ontology, "canonical_business_concepts", None) or []:
                base_concepts_by_uri[bc.concept_uri] = bc

        grounded_mutatable_existing_concepts: set[str] = set()
        for curi, bc in base_concepts_by_uri.items():
            if curi in waived_concept_uris:
                grounded_mutatable_existing_concepts.add(curi)
                continue
            cand_maps: list[ExternalMapping] = []
            for em in getattr(bc, "external_mappings", None) or ():
                coerced = _coerce_fibo_external_mapping(em)
                if coerced is not None:
                    cand_maps.append(coerced)
            cand_maps = _apply_rescores(cand_maps, curi)
            for mop in paired_mapping_ops_by_concept.get(curi, ()):
                coerced_m = _coerce_fibo_external_mapping(mop)
                if coerced_m is not None:
                    cand_maps.append(coerced_m)
            cand_maps = _apply_rescores(cand_maps, curi)
            eff_syns = list(getattr(bc, "synonyms", None) or []) + added_synonyms_by_concept.get(
                curi, []
            )
            if _verify_concept_fibo_grounding(
                curi,
                getattr(bc, "concept_name", "") or "",
                eff_syns,
                cand_maps,
                active_verifier,
            ):
                grounded_mutatable_existing_concepts.add(curi)

        grounded_new_concepts: set[str] = set()
        ungrounded_new_concepts: set[str] = set()
        for op in post_reconcile_ops:
            if getattr(op, "op_type", "") != "add_concept":
                continue
            curi = getattr(op, "concept_uri", "")
            is_grounded = curi in waived_concept_uris
            if not is_grounded:
                new_maps: list[ExternalMapping] = []
                for em in getattr(op, "external_mappings", None) or ():
                    coerced = _coerce_fibo_external_mapping(em)
                    if coerced is not None:
                        new_maps.append(coerced)
                for mop in paired_mapping_ops_by_concept.get(curi, ()):
                    coerced_m = _coerce_fibo_external_mapping(mop)
                    if coerced_m is not None:
                        new_maps.append(coerced_m)
                new_maps = _apply_rescores(new_maps, curi)
                eff_syns = list(
                    getattr(op, "synonyms", None) or []
                ) + added_synonyms_by_concept.get(curi, [])
                is_grounded = _verify_concept_fibo_grounding(
                    curi,
                    getattr(op, "concept_name", "") or "",
                    eff_syns,
                    new_maps,
                    active_verifier,
                )
            if is_grounded:
                grounded_new_concepts.add(curi)
            else:
                ungrounded_new_concepts.add(curi)

        # Also check AddConceptOp.parent_concept_uri dependency closure
        changed = True
        while changed:
            changed = False
            for op in post_reconcile_ops:
                if getattr(op, "op_type", "") != "add_concept":
                    continue
                curi = getattr(op, "concept_uri", "")
                p_uri = getattr(op, "parent_concept_uri", None)
                if p_uri:
                    res_p = _resolve_concept(p_uri)
                    if not res_p:
                        raise DanglingParentReferenceError(
                            f"AddConceptOp '{getattr(op, 'op_id', 'unknown')}' "
                            f"missing parent_concept_uri '{p_uri}'."
                        )
                    if res_p in ungrounded_new_concepts and curi in grounded_new_concepts:
                        grounded_new_concepts.remove(curi)
                        ungrounded_new_concepts.add(curi)
                        changed = True

        valid_concept_endpoints = existing_concepts | grounded_new_concepts

        uncommittable_enums: set[str] = set()
        for op in post_reconcile_ops:
            if getattr(op, "op_type", "") == "add_reference_enumeration":
                euri = getattr(op, "enum_uri", "")
                codes = getattr(op, "allowed_codes", None) or []
                if len(codes) == 0 and enum_code_counts.get(euri, 0) == 0:
                    uncommittable_enums.add(euri)

        committed_ops: list[BaseOntologyDeltaOp] = []
        staged_candidates: list[dict[str, Any]] = []

        def _stage_op(
            staged_op: BaseOntologyDeltaOp,
            provisional_uri: str,
            reason: str,
        ) -> None:
            payload_json = staged_op.model_dump_json()
            scan_redacted_placeholder_leak(payload_json)
            scan_unresolved_pseudonyms(payload_json)
            staged_candidates.append(
                {
                    "candidate_id": f"staged-{getattr(staged_op, 'op_id', 'op')}",
                    "provisional_uri": provisional_uri,
                    "op_type": getattr(staged_op, "op_type", ""),
                    "reason_held_back": reason,
                    "operation": staged_op.model_dump(mode="json", exclude_none=True),
                }
            )

        for op in post_reconcile_ops:
            ot = getattr(op, "op_type", "")
            if ot == "add_concept":
                curi = getattr(op, "concept_uri", "")
                slug = curi.rsplit(":", 1)[-1]
                p_uri = getattr(op, "parent_concept_uri", None)
                if curi in ungrounded_new_concepts:
                    prov_u = _provisional_entity_uri(slug)
                    upd: dict[str, Any] = {"concept_uri": prov_u}
                    if p_uri:
                        res_p = _resolve_concept(p_uri) or p_uri
                        if res_p in ungrounded_new_concepts:
                            upd["parent_concept_uri"] = _provisional_entity_uri(
                                res_p.rsplit(":", 1)[-1]
                            )
                        else:
                            upd["parent_concept_uri"] = res_p
                    prov_op = type(op).model_validate(op.model_dump() | upd)
                    _stage_op(prov_op, prov_u, "ONT_EVO_UNGROUNDED_CONCEPT")
                else:
                    if p_uri:
                        res_p = _resolve_concept(p_uri) or p_uri
                        if res_p != p_uri:
                            op = type(op).model_validate(
                                op.model_dump() | {"parent_concept_uri": res_p}
                            )
                    committed_ops.append(op)

            elif ot == "add_reference_enumeration":
                euri = getattr(op, "enum_uri", "")
                slug = euri.rsplit(":", 1)[-1]
                if euri in uncommittable_enums:
                    prov_eu = _provisional_enum_uri(slug)
                    prov_op = type(op).model_validate(op.model_dump() | {"enum_uri": prov_eu})
                    _stage_op(prov_op, prov_eu, "EMPTY_ENUM_CODES")
                else:
                    committed_ops.append(op)

            elif ot == "add_reference_enum_code":
                euri = getattr(op, "enum_uri", "")
                if euri in uncommittable_enums:
                    slug = euri.rsplit(":", 1)[-1]
                    prov_eu = _provisional_enum_uri(slug)
                    prov_op = type(op).model_validate(
                        op.model_dump() | {"enum_uri": prov_eu, "enumeration_name": ""}
                    )
                    _stage_op(prov_op, prov_eu, "DEPENDENCY_CLOSURE_UNMET")
                else:
                    committed_ops.append(op)

            elif ot in _CONCEPT_CHILD_OP_TYPES:
                curi = getattr(op, "concept_uri", "")
                p_uri = (
                    getattr(op, "parent_concept_uri", None) if ot == "set_parent_concept" else None
                )
                existing_target_ungrounded = (
                    curi in existing_concepts and curi not in grounded_mutatable_existing_concepts
                )
                endpoint_missing = curi not in valid_concept_endpoints or (
                    p_uri is not None and p_uri not in valid_concept_endpoints
                )
                if existing_target_ungrounded or endpoint_missing:
                    upd_child: dict[str, Any] = {}
                    if curi in ungrounded_new_concepts:
                        prov_cu = _provisional_entity_uri(curi.rsplit(":", 1)[-1])
                        upd_child["concept_uri"] = prov_cu
                    elif existing_target_ungrounded:
                        prov_cu = _provisional_entity_uri(curi.rsplit(":", 1)[-1])
                    else:
                        prov_cu = curi
                    if p_uri is not None and p_uri in ungrounded_new_concepts:
                        upd_child["parent_concept_uri"] = _provisional_entity_uri(
                            p_uri.rsplit(":", 1)[-1]
                        )
                    prov_op = (
                        type(op).model_validate(op.model_dump() | upd_child) if upd_child else op
                    )
                    hold_reason = (
                        "ONT_EVO_UNGROUNDED_CONCEPT"
                        if existing_target_ungrounded
                        else "DEPENDENCY_CLOSURE_UNMET"
                    )
                    _stage_op(prov_op, prov_cu, hold_reason)
                else:
                    committed_ops.append(op)

            elif ot == "add_relationship":
                raw_eps = list(getattr(op, "endpoints", None) or [])
                src = getattr(op, "source_concept_uri", "")
                tgt = getattr(op, "target_concept_uri", "")
                check_eps = raw_eps if raw_eps else [src, tgt]
                if any(ep not in valid_concept_endpoints for ep in check_eps):
                    ruri = getattr(op, "relationship_uri", "")
                    r_slug = ruri.rsplit(":", 1)[-1] if ruri else "rel"
                    prov_ru = _provisional_rel_uri(ruri, r_slug)
                    upd_rel: dict[str, Any] = {"relationship_uri": prov_ru}
                    if src in ungrounded_new_concepts:
                        upd_rel["source_concept_uri"] = _provisional_entity_uri(
                            src.rsplit(":", 1)[-1]
                        )
                    if tgt in ungrounded_new_concepts:
                        upd_rel["target_concept_uri"] = _provisional_entity_uri(
                            tgt.rsplit(":", 1)[-1]
                        )
                    if raw_eps:
                        upd_rel["endpoints"] = [
                            _provisional_entity_uri(ep.rsplit(":", 1)[-1])
                            if ep in ungrounded_new_concepts
                            else ep
                            for ep in raw_eps
                        ]
                    prov_op = type(op).model_validate(op.model_dump() | upd_rel)
                    _stage_op(prov_op, prov_ru, "DEPENDENCY_CLOSURE_UNMET")
                else:
                    committed_ops.append(op)
            else:
                committed_ops.append(op)

        op_dependency_order: dict[str, int] = {
            "add_concept": 0,
            "add_reference_enumeration": 0,
            "set_parent_concept": 1,
            "set_concept_elm_archetype": 1,
            "add_attribute": 1,
            "add_relationship": 2,
            "add_reference_enum_code": 3,
            "add_external_mapping": 4,
            "rescore_external_mapping": 5,
            "repin_external_mapping": 5,
        }
        committed_ops.sort(key=lambda op: op_dependency_order.get(getattr(op, "op_type", ""), 6))

        self._verify_additive_only(committed_ops, base_ontology=base_ontology)

        for op in committed_ops:
            op_payload = op.model_dump_json()
            scan_redacted_placeholder_leak(op_payload)
            scan_unresolved_pseudonyms(op_payload)

        if base_ontology is not None:
            base_sha = compute_ontology_sha256(base_ontology)
            base_ver = base_ontology.ontology_version
            if not base_ver:
                raise InvalidOntologyVersionError(
                    "Base ontology is missing required 'ontology_version'"
                )
            target_ver = _compute_target_version(
                base_ver, committed_ops, base_ontology=base_ontology
            )
        else:
            base_ver = "0.0.0"
            base_sha = None
            target_ver = _compute_target_version(base_ver, committed_ops, base_ontology=None)

        op_digs = [
            hashlib.sha256(
                dumps_jcs(op.model_dump(mode="json", exclude_none=True)).encode("utf-8")
            ).hexdigest()
            for op in committed_ops
        ]
        staged_dig = hashlib.sha256(
            json.dumps(staged_candidates, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        delta_content_hash = hashlib.sha256(
            f"{':'.join(op_digs)}|{staged_dig}".encode()
        ).hexdigest()[:16]
        delta_seed = f"{base_sha}:{target_ver}:{delta_content_hash}".encode()
        delta_id = f"delta-{hashlib.sha256(delta_seed).hexdigest()[:16]}"
        proposal_id = f"prop-{hashlib.sha256(delta_id.encode()).hexdigest()[:16]}"

        delta_author = (
            "agent:ontology-architect+caller" if explicit_ops else "agent:ontology-architect"
        )
        delta_meta: dict[str, Any] = {}
        diagnostics: list[str] = []
        if staged_candidates:
            delta_meta["staged_candidates"] = staged_candidates
            diagnostics.append(
                f"Segregated {len(staged_candidates)} ungrounded/dependent candidate operation(s) "
                "into delta.metadata['staged_candidates']."
            )

        delta = OntologyDelta(
            delta_id=delta_id,
            base_ontology_version=base_ver,
            target_ontology_version=target_ver,
            parent_sha256=base_sha,
            author=delta_author,
            operations=committed_ops,
            metadata=delta_meta,
            rationale="Agent-synthesized candidate ontology delta",
        )

        if not committed_ops:
            confidence = 0.0
        else:
            grounded_count = sum(1 for op in committed_ops if getattr(op, "evidence_refs", None))
            ratio = grounded_count / len(committed_ops) if committed_ops else 0.0
            confidence = round(0.70 + 0.20 * ratio, 2)

        return OntologyProposal(
            proposal_id=proposal_id,
            recommender_id="agent:ontology-architect",
            base_ontology_sha256=base_sha or "",
            delta=delta,
            confidence_score=confidence,
            diagnostics=diagnostics,
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
                terms_from_ops = [
                    op.concept_name
                    for op in context.delta.operations
                    if isinstance(op, AddConceptOp)
                ]
                for sc in context.delta.metadata.get("staged_candidates", []):
                    if isinstance(sc, dict):
                        sc_op = sc.get("operation")
                        if isinstance(sc_op, dict) and sc_op.get("op_type") == "add_concept":
                            cname = sc_op.get("concept_name")
                            if isinstance(cname, str) and cname:
                                terms_from_ops.append(cname)
                candidate_terms = terms_from_ops
            elif isinstance(context, OntologyDelta):
                terms_from_delta = [
                    op.concept_name for op in context.operations if isinstance(op, AddConceptOp)
                ]
                for sc in context.metadata.get("staged_candidates", []):
                    if isinstance(sc, dict):
                        sc_op = sc.get("operation")
                        if isinstance(sc_op, dict) and sc_op.get("op_type") == "add_concept":
                            cname = sc_op.get("concept_name")
                            if isinstance(cname, str) and cname:
                                terms_from_delta.append(cname)
                candidate_terms = terms_from_delta
            elif isinstance(context, (list, tuple)) and context:
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

        clean_questions: list[ClarificationQuestion] = []
        for q in questions:
            clean_citations = _clean_evidence_citations(
                q.evidence_citations,
                self.pseudonymizer,
            )
            clean_questions.append(q.model_copy(update={"evidence_citations": clean_citations}))
        return clean_questions

    def _verify_additive_only(
        self,
        operations: Sequence[BaseOntologyDeltaOp],
        base_ontology: GraphAOntology | None = None,
    ) -> None:
        """Validate that proposed delta operations contain strictly allowed additive ops."""
        base_concepts: dict[str, Any] = {}
        if isinstance(base_ontology, GraphAOntology):
            for c in getattr(base_ontology, "canonical_business_concepts", None) or []:
                base_concepts[c.concept_uri] = c

        for op in operations:
            op_type = getattr(op, "op_type", "")
            if op_type not in _ALLOWED_ADDITIVE_OP_TYPES:
                raise AgentDestructiveOpForbiddenError(
                    f"Automated agent proposals must be strictly additive; "
                    f"operation '{op_type}' is forbidden."
                )

            raw_curi = getattr(op, "concept_uri", "")
            canon_curi = _canonical_entity_uri(raw_curi.rsplit(":", 1)[-1]) if raw_curi else ""
            base_c = base_concepts.get(raw_curi) or base_concepts.get(canon_curi)

            if (
                op_type == "add_attribute"
                and base_c is not None
                and not getattr(op, "is_optional", True)
            ):
                raise AgentDestructiveOpForbiddenError(
                    f"Mandatory attribute addition ('{getattr(op, 'attribute_name', '')}') "
                    f"on existing concept '{base_c.concept_uri}' is destructive and forbidden."
                )
            if (
                op_type == "set_parent_concept"
                and base_c is not None
                and base_c.parent_concept_uri is not None
                and base_c.parent_concept_uri != getattr(op, "parent_concept_uri", None)
            ):
                raise AgentDestructiveOpForbiddenError(
                    f"Overwriting or unsetting existing parent_concept_uri on "
                    f"'{base_c.concept_uri}' is destructive and forbidden."
                )
            if (
                op_type == "set_concept_elm_archetype"
                and base_c is not None
                and base_c.elm_archetype is not None
                and base_c.elm_archetype != getattr(op, "elm_archetype", None)
            ):
                raise AgentDestructiveOpForbiddenError(
                    f"Overwriting existing elm_archetype on '{base_c.concept_uri}' "
                    "is destructive and forbidden."
                )
