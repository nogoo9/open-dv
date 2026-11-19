"""Phase 2 GovernanceValidator (ONT-EVO-001..005, Path A & Path B grounding)."""

from __future__ import annotations

from typing import Any

from opendv_contracts.dtos.ontology import (
    ALLOWED_PATH_A_PREDICATES,
    PATH_A_GROUNDING_THRESHOLD,
    AddAttributeOp,
    AddConceptOp,
    OntologyDelta,
    RefineDefinitionOp,
    RemoveAttributeOp,
    RemoveConceptOp,
    RemoveRelationshipOp,
    SetConceptElmArchetypeOp,
    SetParentConceptOp,
    StewardWaiverRecord,
    UpdateConceptUrnOp,
)
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_contracts.spi.deployment import DeploymentStateProvider
from opendv_contracts.spi.grounding import GroundingVerifier
from opendv_contracts.spi.waiver import WaiverVerifier
from opendv_core.models.graph_a import CanonicalBusinessConcept, GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.ontology.matching.verifier import (
    get_default_grounding_verifier,
    is_allowed_path_a_predicate,
)
from opendv_service.ontology.validator_delta import validate_ontology_delta
from opendv_service.ontology.validator_models import (
    _HEX64_RE,
    DEFAULT_GENESIS_SHA256,
    PATH_B_GROUNDING_ORIGINS,
    ConcurrentModificationError,
    ContractViolationError,
    GovernanceValidationResult,
    InvalidGroundingWaiverError,
    UngroundedConceptError,
    _is_concept_deployed,
    _resolve_base_relationships,
    is_concept_mutated,
    is_path_b_empirically_grounded,
)


class GovernanceValidator:
    """Phase 2: Governance guard validator enforcing ONT-EVO-001..005."""

    def __init__(
        self,
        deployment_provider: DeploymentStateProvider | None = None,
        grounding_verifier: GroundingVerifier | None = None,
        waiver_verifier: WaiverVerifier | None = None,
        *,
        strict_verifier_required: bool = True,
        allow_unverified_waivers: bool = False,
    ) -> None:
        self.deployment_provider = deployment_provider
        self.grounding_verifier = (
            grounding_verifier
            if grounding_verifier is not None
            else (get_default_grounding_verifier() if strict_verifier_required else None)
        )
        self.waiver_verifier = waiver_verifier
        self.strict_verifier_required = strict_verifier_required
        self.allow_unverified_waivers = allow_unverified_waivers

    @staticmethod
    def _parse_if_match_digest(if_match: str) -> str:
        """Validate RFC 9110 strong 64-hex ETag or raw 64-hex digest; reject weak/wildcard/lists."""
        raw = if_match.strip()
        if not raw or raw == "*" or "," in raw or raw.startswith(("W/", "w/")):
            raise OpenDVError(
                f"INVALID_PRECONDITION_HEADER: Malformed or weak If-Match header '{if_match}'.",
                error_code=ErrorCode.INVALID_PRECONDITION_HEADER,
            )
        if raw.startswith('"') or raw.endswith('"'):
            if not (raw.startswith('"') and raw.endswith('"') and len(raw) == 66):
                raise OpenDVError(
                    f"INVALID_PRECONDITION_HEADER: Malformed ETag quotes in If-Match '{if_match}'.",
                    error_code=ErrorCode.INVALID_PRECONDITION_HEADER,
                )
            candidate = raw[1:-1]
        else:
            candidate = raw

        if not _HEX64_RE.match(candidate):
            raise OpenDVError(
                f"INVALID_PRECONDITION_HEADER: If-Match '{if_match}' is not a 64-hex SHA-256.",
                error_code=ErrorCode.INVALID_PRECONDITION_HEADER,
            )
        return candidate.lower()

    def validate_occ(
        self,
        delta: OntologyDelta,
        session_head_sha256: str | None = None,
        if_match: str | None = None,
        actual_base_version: str | None = None,
    ) -> None:
        """Enforce ONT-EVO-003: Pure Optimistic Concurrency Control preconditions."""
        delta_base_sha = (delta.base_sha256 or delta.parent_sha256 or "").strip()

        cleaned_if_match: str | None = None
        if if_match is not None:
            cleaned_if_match = self._parse_if_match_digest(if_match)

        clean_delta_ver = (delta.base_version or "").strip().lstrip("v")
        clean_actual_ver = (
            actual_base_version.strip().lstrip("v") if actual_base_version is not None else None
        )

        if clean_actual_ver is not None and clean_delta_ver != clean_actual_ver:
            raise ConcurrentModificationError(
                f"ONT_EVO_CONCURRENT_MODIFICATION: delta base_version '{delta.base_version}' "
                f"does not match actual base version '{actual_base_version}'."
            )

        if session_head_sha256 is not None:
            clean_head_sha = session_head_sha256.strip().lower()
            if delta_base_sha:
                if clean_head_sha != delta_base_sha.lower():
                    raise ConcurrentModificationError(
                        f"ONT_EVO_CONCURRENT_MODIFICATION: session_head_sha256 "
                        f"'{session_head_sha256}' does not match delta base_sha256 "
                        f"'{delta_base_sha}'."
                    )
            else:
                allow_empty_genesis_sha = (clean_actual_ver == "0.0.0") or (
                    clean_actual_ver is None
                    and clean_delta_ver == "0.0.0"
                    and (
                        cleaned_if_match is not None
                        or clean_head_sha == DEFAULT_GENESIS_SHA256.lower()
                    )
                )
                if not allow_empty_genesis_sha:
                    raise ConcurrentModificationError(
                        f"ONT_EVO_CONCURRENT_MODIFICATION: empty delta base_sha256 is not "
                        f"permitted against session_head_sha256 '{session_head_sha256}'."
                    )

        if cleaned_if_match is not None:
            if (
                clean_delta_ver != "0.0.0"
                or (clean_actual_ver is not None and clean_actual_ver != "0.0.0")
                or bool(delta_base_sha)
            ) and cleaned_if_match != delta_base_sha.lower():
                raise ConcurrentModificationError(
                    f"ONT_EVO_CONCURRENT_MODIFICATION: If-Match header '{cleaned_if_match}' "
                    f"does not match delta base_sha256 '{delta_base_sha}'."
                )
            if (
                session_head_sha256 is not None
                and cleaned_if_match != session_head_sha256.strip().lower()
            ):
                raise ConcurrentModificationError(
                    f"ONT_EVO_CONCURRENT_MODIFICATION: If-Match header '{cleaned_if_match}' "
                    f"does not match session head SHA-256 '{session_head_sha256}'."
                )

    def validate_deployed_contracts(
        self,
        base: GraphAOntology,
        delta: OntologyDelta,
    ) -> None:
        """Enforce ONT-EVO-001: Deployed Contract Immutability."""
        if self.deployment_provider is None:
            return

        try:
            deployed_concepts = set(self.deployment_provider.get_deployed_concepts())
            deployed_tables = set(self.deployment_provider.get_deployed_tables())
            deployed_entities = set(self.deployment_provider.get_deployed_entities())
        except Exception as err:
            raise ContractViolationError(
                f"ONT_EVO_CONTRACT_VIOLATION: DeploymentStateProvider error (fails closed): {err}"
            ) from err

        base_concepts = {c.concept_uri: c for c in base.canonical_business_concepts}

        for op in delta.operations:
            if isinstance(op, RemoveConceptOp):
                base_c = base_concepts.get(op.concept_uri)
                if _is_concept_deployed(
                    base_c, op.concept_uri, deployed_concepts, deployed_entities, deployed_tables
                ):
                    hub = base_c.target_hub if base_c else None
                    if hub and hub in deployed_tables:
                        raise ContractViolationError(
                            f"ONT_EVO_CONTRACT_VIOLATION: Cannot remove concept '{op.concept_uri}' "
                            f"bound to deployed hub table '{hub}'."
                        )
                    raise ContractViolationError(
                        f"ONT_EVO_CONTRACT_VIOLATION: Cannot remove deployed concept "
                        f"'{op.concept_uri}'."
                    )

            elif isinstance(op, UpdateConceptUrnOp):
                base_c = base_concepts.get(op.old_concept_uri)
                if _is_concept_deployed(
                    base_c, op.old_concept_uri, deployed_concepts, deployed_entities, deployed_tables
                ):
                    raise ContractViolationError(
                        f"ONT_EVO_CONTRACT_VIOLATION: Cannot rename deployed concept "
                        f"'{op.old_concept_uri}'."
                    )

            elif isinstance(op, RemoveRelationshipOp):
                rel_id = op.relationship_uri or op.relationship_name
                if (op.relationship_uri and op.relationship_uri in deployed_entities) or (
                    op.relationship_name and op.relationship_name in deployed_entities
                ):
                    raise ContractViolationError(
                        f"ONT_EVO_CONTRACT_VIOLATION: Cannot remove deployed relationship '{rel_id}'."
                    )
                for r in _resolve_base_relationships(
                    base, op.relationship_uri, op.relationship_name
                ):
                    if r.relationship_uri in deployed_entities or (
                        r.relationship_name and r.relationship_name in deployed_entities
                    ):
                        raise ContractViolationError(
                            f"ONT_EVO_CONTRACT_VIOLATION: Cannot remove deployed relationship "
                            f"'{r.relationship_uri}'."
                        )
                    if r.target_link and r.target_link in deployed_tables:
                        raise ContractViolationError(
                            f"ONT_EVO_CONTRACT_VIOLATION: Cannot remove relationship "
                            f"'{r.relationship_uri}' bound to deployed link table "
                            f"'{r.target_link}'."
                        )

            elif isinstance(op, RemoveAttributeOp):
                base_c = base_concepts.get(op.concept_uri)
                if _is_concept_deployed(
                    base_c, op.concept_uri, deployed_concepts, deployed_entities, deployed_tables
                ):
                    raise ContractViolationError(
                        f"ONT_EVO_CONTRACT_VIOLATION: Cannot remove attribute "
                        f"'{op.attribute_name}' from deployed concept '{op.concept_uri}'."
                    )

            elif isinstance(op, AddAttributeOp):
                if not op.is_optional and op.concept_uri in base_concepts:
                    base_c = base_concepts[op.concept_uri]
                    if _is_concept_deployed(
                        base_c, op.concept_uri, deployed_concepts, deployed_entities, deployed_tables
                    ):
                        raise ContractViolationError(
                            f"ONT_EVO_CONTRACT_VIOLATION: Cannot add mandatory attribute "
                            f"'{op.attribute_name}' (is_optional=False) to deployed concept "
                            f"'{op.concept_uri}'."
                        )

            elif isinstance(op, AddConceptOp):
                self._check_merge_attrs_deployed(
                    op.concept_uri,
                    op.attributes,
                    base_concepts,
                    deployed_concepts,
                    deployed_entities,
                    deployed_tables,
                    "AddConceptOp",
                )

            elif isinstance(op, RefineDefinitionOp):
                target_u = op.concept_uri or op.target_uri
                self._check_merge_attrs_deployed(
                    target_u,
                    op.additional_attributes,
                    base_concepts,
                    deployed_concepts,
                    deployed_entities,
                    deployed_tables,
                    "RefineDefinitionOp",
                )

            elif isinstance(op, SetParentConceptOp) and op.concept_uri in base_concepts:
                base_c = base_concepts[op.concept_uri]
                existing_parent = base_c.parent_concept_uri
                if (
                    existing_parent is not None
                    and existing_parent != op.parent_concept_uri
                    and _is_concept_deployed(
                        base_c, op.concept_uri, deployed_concepts, deployed_entities, deployed_tables
                    )
                ):
                    raise ContractViolationError(
                        f"ONT_EVO_CONTRACT_VIOLATION: Destructive reparenting of deployed "
                        f"concept '{op.concept_uri}' from '{existing_parent}' to "
                        f"'{op.parent_concept_uri}' is prohibited."
                    )

            elif isinstance(op, SetConceptElmArchetypeOp) and op.concept_uri in base_concepts:
                base_c = base_concepts[op.concept_uri]
                existing_arch = base_c.elm_archetype
                if (
                    existing_arch is not None
                    and existing_arch != op.elm_archetype
                    and _is_concept_deployed(
                        base_c, op.concept_uri, deployed_concepts, deployed_entities, deployed_tables
                    )
                ):
                    raise ContractViolationError(
                        f"ONT_EVO_CONTRACT_VIOLATION: Destructive archetype change on "
                        f"deployed concept '{op.concept_uri}' from '{existing_arch}' to "
                        f"'{op.elm_archetype}' is prohibited."
                    )

    @staticmethod
    def _check_merge_attrs_deployed(
        concept_uri: str,
        raw_attrs: list[Any],
        base_concepts: dict[str, CanonicalBusinessConcept],
        deployed_concepts: set[str],
        deployed_entities: set[str],
        deployed_tables: set[str],
        op_label: str,
    ) -> None:
        if not raw_attrs or concept_uri not in base_concepts:
            return
        base_c = base_concepts[concept_uri]
        existing_names = {a.attribute_name for a in (base_c.attributes or [])}
        for raw_attr in raw_attrs:
            is_opt = (
                raw_attr.get("is_optional", True) is not False
                if isinstance(raw_attr, dict)
                else getattr(raw_attr, "is_optional", True) is not False
            )
            a_name = (
                str(raw_attr.get("attribute_name") or raw_attr.get("name") or "")
                if isinstance(raw_attr, dict)
                else (
                    raw_attr
                    if isinstance(raw_attr, str)
                    else str(getattr(raw_attr, "attribute_name", "") or "")
                )
            ).strip()
            if (
                a_name
                and a_name not in existing_names
                and not is_opt
                and _is_concept_deployed(
                    base_c, concept_uri, deployed_concepts, deployed_entities, deployed_tables
                )
            ):
                raise ContractViolationError(
                    f"ONT_EVO_CONTRACT_VIOLATION: Cannot add mandatory attribute "
                    f"'{a_name}' (is_optional=False) to deployed concept "
                    f"'{concept_uri}' via {op_label}."
                )

    def validate_grounding(
        self,
        base: GraphAOntology,
        delta: OntologyDelta,
        target: GraphAOntology,
        waivers: list[StewardWaiverRecord] | None = None,
        grandfathered_concept_uris: set[str] | frozenset[str] | None = None,
    ) -> None:
        """Enforce ONT-EVO-002/004/005: Path A FIBO >= 0.6500 OR Path B empirical OR Waiver."""
        rename_map: dict[str, str] = {}
        for op in delta.operations:
            if isinstance(op, UpdateConceptUrnOp):
                rename_map[op.old_concept_uri] = op.new_concept_uri

        all_candidate_uris = {
            *(c.concept_uri for c in base.canonical_business_concepts),
            *(c.concept_uri for c in target.canonical_business_concepts),
            *rename_map.keys(),
            *rename_map.values(),
        }

        touched: set[str] = set()
        for u in all_candidate_uris:
            if is_concept_mutated(base, delta, u):
                touched.add(u)
                curr = u
                visited_renames: set[str] = set()
                while curr in rename_map and curr not in visited_renames:
                    visited_renames.add(curr)
                    curr = rename_map[curr]
                    touched.add(curr)

        target_concepts = {c.concept_uri: c for c in target.canonical_business_concepts}
        waiver_list = waivers or []
        _ = grandfathered_concept_uris

        verifier: GroundingVerifier | None = self.grounding_verifier
        if verifier is None and self.strict_verifier_required and touched:
            verifier = get_default_grounding_verifier()

        for concept_uri in sorted(touched):
            if concept_uri not in target_concepts:
                continue
            c = target_concepts[concept_uri]

            # Path A: Verified FIBO Reference Grounding (skos:exactMatch / skos:closeMatch >= 0.6500)
            is_grounded = False
            fibo_candidates = [
                m
                for m in (c.external_mappings or [])
                if (getattr(m, "scheme", "") or "").strip().upper() == "FIBO"
                and is_allowed_path_a_predicate(getattr(m, "predicate", None))
                and (
                    getattr(m, "confidence", None) is None
                    or float(m.confidence) >= PATH_A_GROUNDING_THRESHOLD
                )
            ]
            if verifier is not None:
                if fibo_candidates:
                    res = verifier.verify_grounding(
                        concept_uri,
                        candidates=fibo_candidates,
                        concept_name=c.concept_name,
                        synonyms=tuple(c.synonyms or ()),
                    )
                    if res.is_grounded and res.score >= PATH_A_GROUNDING_THRESHOLD:
                        is_grounded = True
            else:
                for m in fibo_candidates:
                    if (
                        bool(m.target_uri and m.target_uri.strip())
                        and m.confidence is not None
                        and m.confidence >= PATH_A_GROUNDING_THRESHOLD
                    ):
                        is_grounded = True
                        break

            # Path B1: Empirical Evidence Grounding (ONT-EVO-004/005)
            if not is_grounded and is_path_b_empirically_grounded(concept_uri, c, delta):
                is_grounded = True

            # Path B2: Administrative Waiver (strictly admin role + verified token)
            if not is_grounded:
                for w in waiver_list:
                    if concept_uri in w.target_concept_uris:
                        if not w.rationale or len(w.rationale.strip()) < 10:
                            raise InvalidGroundingWaiverError(
                                "INVALID_GROUNDING_WAIVER: Waiver rationale must be at "
                                "least 10 characters."
                            )
                        if w.authorized_role != "admin":
                            raise InvalidGroundingWaiverError(
                                "INVALID_GROUNDING_WAIVER: Waiver authorized_role must be 'admin'."
                            )
                        sig = getattr(w, "signature_or_token", None)
                        if self.waiver_verifier is not None:
                            if sig is not None and not str(sig).strip():
                                raise InvalidGroundingWaiverError(
                                    "INVALID_GROUNDING_WAIVER: Waiver signature_or_token "
                                    "must be non-empty."
                                )
                            valid = self.waiver_verifier.verify_waiver(
                                w,
                                delta_sha256=delta.canonical_sha256(),
                                base_sha256=delta.base_sha256 or compute_ontology_sha256(base),
                            )
                            if not valid:
                                raise InvalidGroundingWaiverError(
                                    "INVALID_GROUNDING_WAIVER: Cryptographic waiver "
                                    "verification failed."
                                )
                        elif not self.allow_unverified_waivers:
                            raise InvalidGroundingWaiverError(
                                "INVALID_GROUNDING_WAIVER: WaiverVerifier is required to "
                                "authorize administrative waivers in governed mode."
                            )
                        is_grounded = True
                        break

            if not is_grounded:
                raise UngroundedConceptError(
                    f"ONT_EVO_UNGROUNDED_CONCEPT: Touched concept '{concept_uri}' lacks verified "
                    "positive reference grounding (BM25 >= 0.6500), Path B empirical evidence, "
                    "and lacks an authorized administrative waiver."
                )

    def validate(
        self,
        base: GraphAOntology,
        delta: OntologyDelta,
        target: GraphAOntology,
        *,
        session_head_sha256: str | None = None,
        if_match: str | None = None,
        actual_base_version: str | None = None,
        waivers: list[StewardWaiverRecord] | None = None,
        grandfathered_concept_uris: set[str] | frozenset[str] | None = None,
    ) -> GovernanceValidationResult:
        """Run complete Phase 2 governance validation."""
        self.validate_occ(
            delta,
            session_head_sha256=session_head_sha256,
            if_match=if_match,
            actual_base_version=actual_base_version,
        )
        self.validate_deployed_contracts(base, delta)
        self.validate_grounding(
            base,
            delta,
            target,
            waivers=waivers,
            grandfathered_concept_uris=grandfathered_concept_uris,
        )
        return GovernanceValidationResult(valid=True, diagnostics=["Governance validation passed."])


__all__ = [
    "ALLOWED_PATH_A_PREDICATES",
    "PATH_A_GROUNDING_THRESHOLD",
    "PATH_B_GROUNDING_ORIGINS",
    "GovernanceValidator",
    "is_path_b_empirically_grounded",
    "validate_ontology_delta",
]
