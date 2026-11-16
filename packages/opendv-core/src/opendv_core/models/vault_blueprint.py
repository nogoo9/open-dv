"""Normative VaultBlueprint envelope and cryptographic lineage tracking (v1.0).

Conforms to ADR-017 and docs/25-data-driven-single-run-pipeline-design.md.
Combines semantic oracle (GraphAOntology v1.0) and physical contract
(NoMistakeDataVault2ContractV5 | None) into a single deterministic envelope
governed by RFC 8785 JSON Canonicalization Scheme (JCS) SHA-256 lineage tracking.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from opendv_core.models.contract_v5 import NoMistakeDataVault2ContractV5
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import (
    canonicalize_graph_a_dict,
    compute_ontology_sha256,
    dumps_jcs_bytes,
)


class StrictBlueprintModel(BaseModel):
    """Base model for VaultBlueprint forbidding undeclared fields."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class BlueprintLineage(StrictBlueprintModel):
    """Cryptographic lineage provenance for a VaultBlueprint (ADR-017)."""

    schema_version: Literal["1.0.0"] = Field(
        default="1.0.0",
        description="Lineage schema version",
    )
    blueprint_revision: int = Field(
        default=1,
        ge=1,
        description="Sequential blueprint revision counter across pipeline runs",
    )
    domain_namespace: str = Field(
        ...,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="Target business domain namespace matching ontology",
    )
    ontology_sha256: str = Field(
        ...,
        pattern=r"^[a-f0-9]{64}$",
        description="Deterministic RFC 8785 JCS SHA-256 of the GraphAOntology",
    )
    contract_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
        description=(
            "Deterministic RFC 8785 JCS SHA-256 of NoMistakeDataVault2ContractV5, "
            "or null on Day-0 seed"
        ),
    )
    parent_blueprint_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
        description="SHA-256 digest of immediate parent VaultBlueprint (null on genesis)",
    )
    lineage_notes: str | None = Field(
        default=None,
        description="Human-readable provenance or evolution note",
    )

    @field_validator("schema_version", mode="before")
    @classmethod
    def _normalize_version(cls, v: Any) -> str:
        if v in ("1.0", "1.0.0"):
            return "1.0.0"
        return str(v)


class VaultBlueprint(StrictBlueprintModel):
    """Unified envelope packaging GraphAOntology, Data Vault 2.0 contract, and lineage (ADR-017)."""

    schema_version: Literal["1.0.0"] = Field(
        default="1.0.0",
        description="Normative VaultBlueprint specification version (v1.0.0)",
    )
    blueprint_version: str | None = Field(
        default=None,
        description="Optional alias for schema_version (e.g. '1.0' or '1.0.0')",
    )
    domain_namespace: str = Field(
        ...,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="Target business domain namespace",
    )
    lineage: BlueprintLineage = Field(
        ...,
        description="Cryptographic lineage metadata chaining parent revisions",
    )
    ontology: GraphAOntology = Field(
        ...,
        description="Semantic oracle: GraphAOntology v1.0",
    )
    contract: NoMistakeDataVault2ContractV5 | None = Field(
        default=None,
        description=(
            "Physical Data Vault 2.0 model contract (or null for zero-data greenfield seed)"
        ),
    )

    @field_validator("schema_version", mode="before")
    @classmethod
    def _normalize_version(cls, v: Any) -> str:
        if v in ("1.0", "1.0.0"):
            return "1.0.0"
        return str(v)

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
    ) -> VaultBlueprint:
        if isinstance(obj, (dict, Mapping)):
            bv = obj.get("blueprint_version")
            sv = obj.get("schema_version")
            if bv and not sv:
                obj = dict(obj)
                obj["schema_version"] = "1.0.0" if bv in ("1.0", "1.0.0") else bv
            elif sv and not bv:
                obj = dict(obj)
                obj["blueprint_version"] = sv
        return super().model_validate(
            obj, strict=strict, from_attributes=from_attributes, context=context
        )


def _strip_none_deep(obj: Any) -> Any:
    """Recursively strip None values from mappings and sequences of mappings."""
    if isinstance(obj, Mapping):
        return {k: _strip_none_deep(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_none_deep(item) for item in obj if item is not None]
    return obj


def compute_contract_sha256(
    contract: NoMistakeDataVault2ContractV5 | Mapping[str, Any] | None,
) -> str | None:
    """Compute deterministic RFC 8785 JCS SHA-256 of NoMistakeDataVault2ContractV5, or None."""
    if contract is None:
        return None
    if isinstance(contract, BaseModel):
        data = contract.model_dump(mode="json", exclude_none=True)
    elif isinstance(contract, Mapping):
        try:
            data = NoMistakeDataVault2ContractV5.model_validate(contract).model_dump(
                mode="json", exclude_none=True
            )
        except (ValidationError, TypeError, ValueError):
            data = _strip_none_deep(contract)
    else:
        raise TypeError(
            f"Expected NoMistakeDataVault2ContractV5 or Mapping, got {type(contract).__name__}"
        )
    jcs_bytes = dumps_jcs_bytes(data)
    return hashlib.sha256(jcs_bytes).hexdigest().lower()


def compute_lineage_sha256(lineage: BlueprintLineage | Mapping[str, Any]) -> str:
    """Compute deterministic RFC 8785 JCS SHA-256 of BlueprintLineage."""
    if isinstance(lineage, BaseModel):
        data = lineage.model_dump(mode="json", exclude_none=True)
    elif isinstance(lineage, Mapping):
        try:
            data = BlueprintLineage.model_validate(lineage).model_dump(
                mode="json", exclude_none=True
            )
        except (ValidationError, TypeError, ValueError):
            data = _strip_none_deep(lineage)
    else:
        raise TypeError(
            f"Expected BlueprintLineage or Mapping, got {type(lineage).__name__}"
        )
    jcs_bytes = dumps_jcs_bytes(data)
    return hashlib.sha256(jcs_bytes).hexdigest().lower()


def canonicalize_vault_blueprint_dict(
    blueprint: VaultBlueprint | Mapping[str, Any],
) -> dict[str, Any]:
    """Convert a VaultBlueprint or dict to a canonical dictionary for RFC 8785 serialization."""
    if isinstance(blueprint, VaultBlueprint):
        raw = blueprint.model_dump(mode="json", by_alias=False, exclude_none=True)
    elif isinstance(blueprint, Mapping):
        try:
            raw = VaultBlueprint.model_validate(blueprint).model_dump(
                mode="json", by_alias=False, exclude_none=True
            )
        except (ValidationError, TypeError, ValueError):
            raw = _strip_none_deep(blueprint)
    else:
        raise TypeError(
            f"Expected VaultBlueprint or Mapping, got {type(blueprint).__name__}"
        )

    lineage_raw = raw.get("lineage")
    if isinstance(lineage_raw, Mapping):
        try:
            canonical_lineage = BlueprintLineage.model_validate(lineage_raw).model_dump(
                mode="json", exclude_none=True
            )
        except (ValidationError, TypeError, ValueError):
            canonical_lineage = _strip_none_deep(lineage_raw)
    elif isinstance(lineage_raw, BaseModel):
        canonical_lineage = lineage_raw.model_dump(mode="json", exclude_none=True)
    else:
        canonical_lineage = lineage_raw

    canonical: dict[str, Any] = {
        "schema_version": "1.0.0",
        "domain_namespace": raw["domain_namespace"],
        "lineage": canonical_lineage,
        "ontology": canonicalize_graph_a_dict(raw["ontology"]),
    }
    if "contract" in raw and raw["contract"] is not None:
        contract_raw = raw["contract"]
        if isinstance(contract_raw, Mapping):
            try:
                canonical["contract"] = NoMistakeDataVault2ContractV5.model_validate(
                    contract_raw
                ).model_dump(mode="json", exclude_none=True)
            except (ValidationError, TypeError, ValueError):
                canonical["contract"] = _strip_none_deep(contract_raw)
        elif isinstance(contract_raw, BaseModel):
            canonical["contract"] = contract_raw.model_dump(mode="json", exclude_none=True)
        else:
            canonical["contract"] = contract_raw
    return canonical


def compute_blueprint_sha256(blueprint: VaultBlueprint | Mapping[str, Any]) -> str:
    """Compute deterministic RFC 8785 JCS SHA-256 hex digest of a VaultBlueprint."""
    canonical_dict = canonicalize_vault_blueprint_dict(blueprint)
    jcs_bytes = dumps_jcs_bytes(canonical_dict)
    return hashlib.sha256(jcs_bytes).hexdigest().lower()


def create_vault_blueprint(
    domain_namespace: str,
    ontology: GraphAOntology | Mapping[str, Any],
    contract: NoMistakeDataVault2ContractV5 | Mapping[str, Any] | None = None,
    *,
    parent_blueprint_sha256: str | None = None,
    blueprint_revision: int = 1,
    lineage_notes: str | None = None,
) -> VaultBlueprint:
    """Convenience factory creating a VaultBlueprint with automatically computed lineage hashes."""
    if isinstance(ontology, Mapping):
        ontology_model = GraphAOntology.model_validate(ontology)
    else:
        ontology_model = ontology

    if isinstance(contract, Mapping):
        contract_model: NoMistakeDataVault2ContractV5 | None = (
            NoMistakeDataVault2ContractV5.model_validate(contract)
        )
    else:
        contract_model = contract

    ontology_sha = compute_ontology_sha256(ontology_model)
    contract_sha = compute_contract_sha256(contract_model)

    lineage = BlueprintLineage(
        schema_version="1.0.0",
        blueprint_revision=blueprint_revision,
        domain_namespace=domain_namespace,
        ontology_sha256=ontology_sha,
        contract_sha256=contract_sha,
        parent_blueprint_sha256=parent_blueprint_sha256,
        lineage_notes=lineage_notes,
    )

    return VaultBlueprint(
        schema_version="1.0.0",
        domain_namespace=domain_namespace,
        lineage=lineage,
        ontology=ontology_model,
        contract=contract_model,
    )


__all__ = [
    "BlueprintLineage",
    "StrictBlueprintModel",
    "VaultBlueprint",
    "canonicalize_vault_blueprint_dict",
    "compute_blueprint_sha256",
    "compute_contract_sha256",
    "compute_lineage_sha256",
    "create_vault_blueprint",
]
