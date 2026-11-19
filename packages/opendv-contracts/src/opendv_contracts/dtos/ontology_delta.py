from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import (
    AliasChoices,
    AwareDatetime,
    Field,
    ValidationInfo,
    model_validator,
)

from opendv_contracts.dtos.common import BaseContractModel
from opendv_contracts.dtos.ontology_ops_base import (
    SEMVER_RANKS,
    AddConceptOp,
    AddRelationshipOp,
    SemVerClass,
    _assert_no_alias_conflict,
)
from opendv_contracts.dtos.ontology_ops_minor import (
    AddReferenceEnumerationOp,
)
from opendv_contracts.dtos.ontology_ops_patch import (
    AddSourceHintOp,
    OntologyOperation,
)
from opendv_contracts.jcs import dumps_jcs


class OntologyDelta(BaseContractModel):
    """Ordered collection of additive and structural operations evolving Graph A to vN+1."""

    delta_id: str = Field(
        ...,
        min_length=1,
        description="Unique delta identifier",
    )
    parent_sha256: str | None = Field(
        default=None,
        pattern=r"^([a-f0-9]{64}|)$",
        validation_alias=AliasChoices("parent_sha256", "base_sha256"),
        description="Parent Graph A CAS digest precondition",
    )
    domain_namespace: str | None = Field(
        default=None,
        pattern=r"^([a-z][a-z0-9_]*)?$",
        description="Domain namespace matching base Graph A",
    )
    base_ontology_version: str = Field(
        default="1.0.0",
        pattern=r"^([0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?)$",
        validation_alias=AliasChoices("base_ontology_version", "base_version"),
        description="Base ontology version precondition",
    )
    target_ontology_version: str | None = Field(
        default="1.1.0",
        pattern=r"^([0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?)$",
        validation_alias=AliasChoices("target_ontology_version", "target_version"),
        description="Target ontology version after delta application",
    )
    operations: list[OntologyOperation] = Field(
        default_factory=list,
        description="Ordered sequence of ontology operations",
    )
    created_at: AwareDatetime | None = Field(
        default=None,
        description="UTC creation timestamp",
    )
    author: str | None = Field(
        default=None,
        description="Author or agent identity that created the delta",
    )
    rationale: str | None = Field(
        default=None,
        description="Overall business rationale for delta",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary authoring provenance metadata",
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_conflicting_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _assert_no_alias_conflict(data, "base_sha256", "parent_sha256")
            _assert_no_alias_conflict(data, "base_version", "base_ontology_version")
            _assert_no_alias_conflict(data, "target_version", "target_ontology_version")
        return data

    @model_validator(mode="after")
    def _validate_occ_preconditions(self) -> OntologyDelta:
        base_ver = self.base_ontology_version or "0.0.0"
        base_match = re.match(r"^(\d+)\.(\d+)\.(\d+)", base_ver)
        b_maj = int(base_match.group(1)) if base_match else 1

        if b_maj == 0:
            # On 0.x.y genesis/pre-CBC drafts, parent_sha256 may be None, empty "",
            # or a valid 64-character lowercase hex SHA-256 (validated by field pattern).
            return self

        is_v2_envelope = bool(self.author or self.created_at or self.rationale)
        if (is_v2_envelope or self.parent_sha256 is not None) and not self.parent_sha256:
            raise ValueError(
                f"base_sha256 must be a 64-character hex string when base_version "
                f"is '{self.base_ontology_version}' (empty base_sha256 is only allowed when "
                "base_version major is 0, e.g. '0.0.0')"
            )
        return self

    @model_validator(mode="after")
    def _validate_semver_bump(self, info: ValidationInfo) -> OntologyDelta:
        """Enforce monotonic SemVer bump invariants between base and target ontology versions.

        Note on Legacy Wave-1 IP-1 Cold-Start Bootstrap:
            DTO deserialization permits narrowly scoped legacy IP-1 v1 cold-start proposals
            (`1.0.0 -> 1.0.0`, unanchored `parent_sha256`, `metadata.mode == "BOOTSTRAP"`,
            no Studio v2 envelope fields, and only the 7 additive Wave-1 v1 operations) to
            cross the wire on `OntologyProposal` and `OntologyDecisionRequest` for
            `execute_ontology_decision` via trusted `context={"allow_bootstrap": True}`.
            Studio runtime commit/apply (`GovernanceValidator`, `apply()`,
            `AtomicCommitService`) strictly rejects `1.0.0 -> 1.0.0` deltas unless
            `allow_legacy_ip1_bootstrap=True` is passed by trusted legacy IP-1 server code.
        """
        base_ver = self.base_ontology_version or "0.0.0"
        req = self.highest_semver_class()

        if req == "MAJOR":
            if not self.target_ontology_version:
                raise ValueError(
                    "target_ontology_version is required when delta contains "
                    "MAJOR breaking operations"
                )
            if base_ver == "0.0.0":
                raise ValueError(
                    "Destructive operations (MAJOR) are prohibited on greenfield genesis "
                    "delta (base_version '0.0.0')"
                )

        if self.target_ontology_version and self.base_ontology_version:
            base_match = re.match(r"^(\d+)\.(\d+)\.(\d+)", self.base_ontology_version)
            target_match = re.match(r"^(\d+)\.(\d+)\.(\d+)", self.target_ontology_version)
            if base_match and target_match:
                b_maj, b_min, b_pat = map(int, base_match.groups())
                t_maj, t_min, t_pat = map(int, target_match.groups())

                # Trusted server-side context override only (never caller payload metadata)
                allow_bootstrap = bool(
                    isinstance(info.context, dict) and info.context.get("allow_bootstrap") is True
                )
                if (
                    allow_bootstrap
                    and self.base_ontology_version == "1.0.0"
                    and self.target_ontology_version == "1.0.0"
                    and req != "MAJOR"
                ):
                    return self

                if not self.operations:
                    if (t_maj, t_min, t_pat) < (b_maj, b_min, b_pat):
                        raise ValueError(
                            f"Target version '{self.target_ontology_version}' cannot be less than "
                            f"base '{self.base_ontology_version}'."
                        )
                    return self

                if b_maj == 0:
                    if (t_maj, t_min, t_pat) <= (b_maj, b_min, b_pat):
                        if self.base_ontology_version == "0.0.0":
                            raise ValueError(
                                "target_ontology_version must be greater than base '0.0.0'"
                            )
                        raise ValueError(
                            f"Target version '{self.target_ontology_version}' must be strictly "
                            f"greater than base '{self.base_ontology_version}'."
                        )
                    has_add_concept = any(isinstance(op, AddConceptOp) for op in self.operations)
                    if has_add_concept:
                        if (
                            t_maj > 1
                            or (t_maj == 1 and (t_min, t_pat) != (0, 0))
                            or (t_maj == 0 and t_min <= b_min)
                        ):
                            raise ValueError(
                                f"Target version '{self.target_ontology_version}' is invalid for "
                                f"pre-release base '{self.base_ontology_version}' introducing "
                                f"concepts. Expected initial release '1.0.0' or draft minor bump "
                                f"'0.{b_min + 1}.0'."
                            )
                    else:
                        if t_maj != 0:
                            raise ValueError(
                                f"Target version '{self.target_ontology_version}' is invalid for "
                                f"pre-CBC draft base '{self.base_ontology_version}' without "
                                "AddConceptOp. Expected 0.y.z draft version."
                            )
                        if req in ("MAJOR", "MINOR") and t_min <= b_min:
                            raise ValueError(
                                f"Target version '{self.target_ontology_version}' is invalid for "
                                f"draft base '{self.base_ontology_version}' with {req} operations. "
                                f"Expected draft minor bump (>= 0.{b_min + 1}.0)."
                            )
                elif req == "MAJOR":
                    if t_maj <= b_maj:
                        raise ValueError(
                            f"Target version '{self.target_ontology_version}' is invalid for "
                            f"base '{self.base_ontology_version}' with MAJOR breaking operations. "
                            f"Expected major bump (>= {b_maj + 1}.0.0)."
                        )
                elif req == "MINOR":
                    if (t_maj < b_maj) or (t_maj == b_maj and t_min <= b_min):
                        raise ValueError(
                            f"Target version '{self.target_ontology_version}' is invalid for "
                            f"base '{self.base_ontology_version}' with MINOR operations. "
                            f"Expected minor or major bump (>= {b_maj}.{b_min + 1}.0)."
                        )
                elif req == "PATCH" and (t_maj, t_min, t_pat) <= (b_maj, b_min, b_pat):
                    raise ValueError(
                        f"Target version '{self.target_ontology_version}' is invalid for "
                        f"base '{self.base_ontology_version}'. "
                        "Target must be strictly greater than base."
                    )
        return self

    @property
    def base_sha256(self) -> str:
        return self.parent_sha256 or ""

    @property
    def base_version(self) -> str:
        return self.base_ontology_version or "0.0.0"

    @property
    def target_version(self) -> str | None:
        return self.target_ontology_version

    def highest_semver_class(self, base_ontology: Any = None) -> SemVerClass:
        """Determine the highest SemVer classification across all operations."""
        if not self.operations:
            return "PATCH"
        ranks = [SEMVER_RANKS[op.effective_semver_class(base_ontology)] for op in self.operations]
        highest_rank = max(ranks)
        for s_class, rank in SEMVER_RANKS.items():
            if rank == highest_rank:
                return s_class
        return "PATCH"

    def canonical_dict(self) -> dict[str, Any]:
        """Return the dictionary representation for canonical hashing (excluding None fields)."""
        return self.model_dump(mode="json", exclude_none=True)

    def canonical_json(self) -> str:
        """Serialize delta to RFC 8785 canonical JSON string."""
        return dumps_jcs(self.canonical_dict())

    def canonical_sha256(self) -> str:
        """Compute RFC 8785 canonical SHA-256 digest of the delta."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def to_v1_schema_dict(self) -> dict[str, Any]:
        """Export dictionary conforming strictly to schemas/v1/ontology-delta.schema.json.

        Raises:
            ValueError: If non-v1 operations or populated extension fields are present.
        """
        if self.author is not None:
            raise ValueError(
                "Cannot convert delta to v1 schema: 'author' envelope field is populated"
            )
        if self.rationale is not None:
            raise ValueError(
                "Cannot convert delta to v1 schema: 'rationale' envelope field is populated"
            )
        if self.created_at is not None:
            raise ValueError(
                "Cannot convert delta to v1 schema: 'created_at' envelope field is populated"
            )

        v1_op_allowed: dict[str, set[str]] = {
            "add_concept": {
                "op_id",
                "op_type",
                "rationale",
                "confidence",
                "evidence_refs",
                "concept_uri",
                "concept_name",
                "elm_archetype",
                "definition",
                "description",
                "target_hub",
                "attributes",
                "governance_constraints",
                "key_authority_tiers",
            },
            "add_relationship": {
                "op_id",
                "op_type",
                "rationale",
                "confidence",
                "evidence_refs",
                "relationship_uri",
                "relationship_name",
                "target_link",
                "source_concept_uri",
                "target_concept_uri",
                "endpoints",
                "grain_semantics",
                "description",
                "yagni_cost_guard_policy",
            },
            "add_reference_enumeration": {
                "op_id",
                "op_type",
                "rationale",
                "confidence",
                "evidence_refs",
                "enum_uri",
                "name",
                "target_reference_table",
                "values",
                "allowed_codes",
                "description",
            },
            "add_key_authority_pattern": {
                "op_id",
                "op_type",
                "rationale",
                "confidence",
                "evidence_refs",
                "concept_uri",
                "authority_name",
                "pattern_regex",
                "tier",
                "tier_spec",
                "description",
            },
            "add_governance_constraint": {
                "op_id",
                "op_type",
                "rationale",
                "confidence",
                "evidence_refs",
                "concept_uri",
                "constraint_id",
                "constraint_type",
                "pii_confidential_attributes",
                "mnpi_confidential_attributes",
                "required_satellite_split",
                "description",
            },
            "refine_definition": {
                "op_id",
                "op_type",
                "rationale",
                "confidence",
                "evidence_refs",
                "target_uri",
                "concept_uri",
                "refined_description",
                "additional_attributes",
            },
            "add_source_hint": {
                "op_id",
                "op_type",
                "rationale",
                "confidence",
                "evidence_refs",
                "concept_uri",
                "source_name",
                "source_system",
                "table_name",
                "column_hint",
                "column_name",
            },
        }

        # Check for non-v1 operations
        for op in self.operations:
            if op.op_type not in v1_op_allowed:
                raise ValueError(
                    f"Operation type '{op.op_type}' is not supported in v1 schema "
                    "(only 7 additive op types allowed in v1)"
                )
            if isinstance(op, AddConceptOp):
                if op.parent_concept_uri is not None:
                    raise ValueError(
                        "Cannot convert AddConceptOp to v1 schema: "
                        "'parent_concept_uri' is populated"
                    )
                if op.synonyms is not None:
                    raise ValueError(
                        "Cannot convert AddConceptOp to v1 schema: 'synonyms' is populated"
                    )
                if op.external_mappings is not None:
                    raise ValueError(
                        "Cannot convert AddConceptOp to v1 schema: 'external_mappings' is populated"
                    )
                if op.source_hints is not None:
                    raise ValueError(
                        "Cannot convert AddConceptOp to v1 schema: 'source_hints' is populated"
                    )
                if op.metadata is not None:
                    raise ValueError(
                        "Cannot convert AddConceptOp to v1 schema: 'metadata' is populated"
                    )
            elif isinstance(op, AddRelationshipOp):
                if op.cardinality is not None:
                    raise ValueError(
                        "Cannot convert AddRelationshipOp to v1 schema: 'cardinality' is populated"
                    )
                if op.metadata is not None:
                    raise ValueError(
                        "Cannot convert AddRelationshipOp to v1 schema: 'metadata' is populated"
                    )
            elif isinstance(op, AddReferenceEnumerationOp):
                if op.code_descriptions is not None:
                    raise ValueError(
                        "Cannot convert AddReferenceEnumerationOp to v1 schema: "
                        "'code_descriptions' is populated"
                    )
                if op.metadata is not None:
                    raise ValueError(
                        "Cannot convert AddReferenceEnumerationOp to v1 schema: "
                        "'metadata' is populated"
                    )
            elif isinstance(op, AddSourceHintOp):
                if op.hint is not None:
                    raise ValueError(
                        "Cannot convert AddSourceHintOp to v1 schema: 'hint' is populated"
                    )

        pruned_ops: list[dict[str, Any]] = []
        for op in self.operations:
            op_dict = op.model_dump(mode="json", exclude_none=True)
            allowed = v1_op_allowed[op.op_type]
            pruned_ops.append({k: v for k, v in op_dict.items() if k in allowed})

        result: dict[str, Any] = {
            "delta_id": self.delta_id,
            "parent_sha256": self.parent_sha256 or None,
            "domain_namespace": self.domain_namespace,
            "base_ontology_version": (
                self.base_ontology_version
                if (self.base_ontology_version and self.base_ontology_version != "0.0.0")
                else None
            ),
            "target_ontology_version": self.target_ontology_version,
            "operations": pruned_ops,
            "metadata": self.metadata,
        }
        return {k: v for k, v in result.items() if v is not None}


