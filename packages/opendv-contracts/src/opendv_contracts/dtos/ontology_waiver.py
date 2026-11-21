from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import UUID4, AwareDatetime, Field, model_validator

from opendv_contracts.dtos.common import BaseContractModel
from opendv_contracts.dtos.ontology_delta import OntologyDelta
from opendv_contracts.dtos.ontology_ops_base import CONCEPT_URN_REGEX, BaseOntologyDeltaOp
from opendv_contracts.jcs import dumps_jcs


class StewardWaiverRecord(BaseContractModel):
    """Cryptographically verifiable administrative waiver for ungrounded concept evolution."""

    waiver_id: UUID4 = Field(default_factory=uuid4, description="Unique waiver identifier")
    proposal_id: UUID4 = Field(..., description="Referenced ontology proposal identifier")
    proposal_delta_sha256: str = Field(
        ...,
        pattern=r"^[a-f0-9]{64}$",
        description="SHA-256 digest of proposed delta",
    )
    target_concept_uris: list[str] = Field(
        ...,
        min_length=1,
        description="Target concept URIs exempted from grounding check",
    )
    base_sha256: str = Field(
        ...,
        pattern=r"^[a-f0-9]{64}$",
        description="SHA-256 digest of base ontology precondition",
    )
    rationale: str = Field(
        ...,
        min_length=10,
        description="Business and architectural justification for waiver (>= 10 chars)",
    )
    authorized_by: str = Field(
        ..., min_length=1, description="Identity of approving administrator or steward"
    )
    authorized_role: Literal["admin", "steward"] = Field(
        ..., description="Role authorization tier for waiver approval"
    )
    signature_or_token: str = Field(
        ...,
        min_length=1,
        repr=False,
        description="Cryptographic signature or authorization token",
    )
    created_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of authorization",
    )
    expires_at: AwareDatetime | None = Field(
        default=None,
        description="Optional expiration timestamp for time-bounded waiver",
    )

    @model_validator(mode="after")
    def _validate_expiration_and_targets(self) -> StewardWaiverRecord:
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError(
                f"expires_at ({self.expires_at.isoformat()}) must be strictly after "
                f"created_at ({self.created_at.isoformat()})"
            )
        for uri in self.target_concept_uris:
            if not re.match(CONCEPT_URN_REGEX, uri):
                raise ValueError(
                    f"target_concept_uri '{uri}' does not match pattern {CONCEPT_URN_REGEX}"
                )
        return self

    def signing_payload(self) -> str:
        """Compute the RFC 8785 canonical JSON signing payload."""
        created_str = self.created_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        expires_str = (
            self.expires_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            if self.expires_at
            else None
        )
        payload = {
            "authorized_by": self.authorized_by,
            "authorized_role": self.authorized_role,
            "base_sha256": self.base_sha256,
            "created_at": created_str,
            "expires_at": expires_str,
            "proposal_delta_sha256": self.proposal_delta_sha256,
            "proposal_id": str(self.proposal_id),
            "rationale": self.rationale,
            "target_concept_uris": sorted(set(self.target_concept_uris)),
            "waiver_id": str(self.waiver_id),
        }
        return dumps_jcs(payload)

    def signing_sha256(self) -> str:
        """Compute SHA-256 digest of canonical signing payload."""
        return hashlib.sha256(self.signing_payload().encode("utf-8")).hexdigest()


LEGACY_WAVE1_V1_OP_TYPES: frozenset[str] = frozenset(
    {
        "add_concept",
        "add_relationship",
        "add_reference_enumeration",
        "add_key_authority_pattern",
        "add_governance_constraint",
        "refine_definition",
        "add_source_hint",
    }
)


def _extract_raw_op_type(op: Any) -> str | None:
    """Extract `op_type` from a raw operation dict or `BaseOntologyDeltaOp` instance."""
    if isinstance(op, BaseOntologyDeltaOp):
        return op.op_type
    if isinstance(op, dict):
        op_type = op.get("op_type")
        return op_type if isinstance(op_type, str) else None
    return None


def _try_validate_bootstrap_delta_field(v: Any) -> OntologyDelta | None:
    """Validate narrow Wave-1 legacy IP-1 cold-start BOOTSTRAP 1.0.0->1.0.0 deltas.

    Applies exclusively to `OntologyProposal` and `OntologyDecisionRequest` for
    `PAR-ONT-BOOTSTRAP` backward compatibility when all of the following hold:
    1. `parent_sha256` (and `base_sha256`) is falsy, `base_ontology_version == "1.0.0"`,
       `target_ontology_version == "1.0.0"`, and `metadata.mode == "BOOTSTRAP"`.
    2. None of the Studio v2 envelope fields (`author`, `created_at`, `rationale`) are
       populated.
    3. Every operation's `op_type` belongs to the 7 legacy Wave-1 additive v1 op types
       (`LEGACY_WAVE1_V1_OP_TYPES`) and none is `MAJOR`.

    DTO deserialization permits these legacy IP-1 v1 cold-start proposals to cross the wire
    for `execute_ontology_decision`, while Studio runtime commit/apply
    (`GovernanceValidator`, `apply()`, `AtomicCommitService`) strictly rejects
    `1.0.0 -> 1.0.0` deltas unless `allow_legacy_ip1_bootstrap=True` is passed by trusted
    legacy IP-1 server code.
    """
    if v is None:
        return None
    raw_dict: dict[str, Any] | None = None
    if isinstance(v, OntologyDelta):
        if (
            not v.parent_sha256
            and v.base_ontology_version == "1.0.0"
            and v.target_ontology_version == "1.0.0"
            and isinstance(v.metadata, dict)
            and v.metadata.get("mode") == "BOOTSTRAP"
            and v.author is None
            and v.created_at is None
            and v.rationale is None
            and all(op.op_type in LEGACY_WAVE1_V1_OP_TYPES for op in v.operations)
            and v.highest_semver_class() != "MAJOR"
        ):
            raw_dict = v.model_dump(mode="python")
    elif isinstance(v, dict):
        base_ver = (
            v.get("base_ontology_version")
            if v.get("base_ontology_version") is not None
            else v.get("base_version")
        )
        target_ver = (
            v.get("target_ontology_version")
            if v.get("target_ontology_version") is not None
            else v.get("target_version")
        )
        meta = v.get("metadata")
        ops = v.get("operations", [])
        if (
            not v.get("parent_sha256")
            and not v.get("base_sha256")
            and base_ver == "1.0.0"
            and target_ver == "1.0.0"
            and isinstance(meta, dict)
            and meta.get("mode") == "BOOTSTRAP"
            and v.get("author") is None
            and v.get("created_at") is None
            and v.get("rationale") is None
            and isinstance(ops, (list, tuple))
            and all(_extract_raw_op_type(op) in LEGACY_WAVE1_V1_OP_TYPES for op in ops)
        ):
            raw_dict = v

    if raw_dict is not None:
        validated = OntologyDelta.model_validate(raw_dict, context={"allow_bootstrap": True})
        if (
            all(op.op_type in LEGACY_WAVE1_V1_OP_TYPES for op in validated.operations)
            and validated.highest_semver_class() != "MAJOR"
        ):
            return validated
    return None


