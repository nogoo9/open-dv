"""Shared Studio delta normalization, version loading, and SemVer helpers."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter, ValidationError

from opendv_contracts.dtos.ontology import BaseOntologyDeltaOp, OntologyDelta, OntologyOperation
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.genesis import GENESIS_VERSION, create_genesis_ontology
from opendv_core.models.graph_a import GraphAOntology
from opendv_service.ontology.applier import compute_next_semver
from opendv_service.studio.atomic_commit import parse_semver_tuple, parse_strong_etag
from opendv_service.studio.session_manager import (
    SessionMetadata,
    normalize_version_tag,
    sanitize_domain_namespace,
)

_OP_TYPE_ADAPTER: TypeAdapter[OntologyOperation] = TypeAdapter(OntologyOperation)
_VERSION_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:v)?[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9_.-]+)?$"
)


class StudioWorkflowError(OpenDVError):
    """Typed Studio workflow error carrying catalog code, HTTP status, and CLI exit code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        exit_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        err_enum = ErrorCode._value2member_map_.get(code, ErrorCode.INTERNAL_ERROR)
        super().__init__(message, error_code=err_enum, details=details)
        self.code = code
        if status_code is not None:
            self.http_status = status_code
        if exit_code is not None:
            self.exit_code = exit_code


def raise_from_opendv_error(exc: OpenDVError) -> StudioWorkflowError:
    """Wrap any OpenDVError preserving error_code, http_status, exit_code, and details."""
    if isinstance(exc, StudioWorkflowError):
        return exc
    raw_code = getattr(exc, "error_code", ErrorCode.INTERNAL_ERROR)
    code_str = raw_code.value if hasattr(raw_code, "value") else str(raw_code)
    details = getattr(exc, "details", None)
    return StudioWorkflowError(
        code_str,
        str(getattr(exc, "message", None) or str(exc)),
        status_code=getattr(exc, "http_status", None),
        exit_code=getattr(exc, "exit_code", None),
        details=dict(details) if isinstance(details, dict) and details else None,
    )


def parse_strong_etag_or_error(raw_etag: str, header_name: str = "If-Match") -> str:
    """Parse strong 64-hex ETag or raise INVALID_PRECONDITION_HEADER."""
    try:
        return parse_strong_etag(raw_etag)
    except OpenDVError as exc:
        raise StudioWorkflowError(
            "INVALID_PRECONDITION_HEADER",
            f"Invalid {header_name} precondition '{raw_etag}': {exc}",
            status_code=400,
            exit_code=2,
        ) from exc


def validate_version_syntax(version: str | None) -> None:
    """Validate SemVer string format."""
    if version is not None and (
        not isinstance(version, str) or not _VERSION_PATTERN.match(version)
    ):
        raise StudioWorkflowError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid version identifier '{version}'.",
            status_code=400,
            exit_code=2,
        )


def derive_genesis_target_version(operations: list[Any]) -> str:
    """Derive genesis SemVer target based on whether canonical concepts are added."""
    has_cbc = any(
        (isinstance(op, dict) and op.get("op_type") in ("add_concept", "AddConceptOp"))
        or getattr(op, "op_type", None) in ("add_concept", "AddConceptOp")
        for op in operations
    )
    return "1.0.0" if has_cbc else "0.1.0"


def _normalize_urn(
    raw_uri: str | None,
    fallback_name: str | None,
    default_prefix: str,
    valid_schemes: tuple[str, ...],
) -> str | None:
    if not raw_uri and fallback_name:
        return f"{default_prefix}{fallback_name.lower().replace(' ', '_')}"
    if raw_uri and not any(raw_uri.startswith(s) for s in valid_schemes) and ":" not in raw_uri:
        return f"{default_prefix}{raw_uri}"
    return raw_uri


def normalize_delta_operation_wire(op: Any) -> Any:
    """Normalize operation dict from agent or wire representation to normative contract format."""
    if not isinstance(op, dict):
        return op
    op_dict = dict(op)
    if not str(op_dict.get("op_id") or "").strip():
        op_dict["op_id"] = f"op-{uuid.uuid4().hex[:8]}"
    op_type = op_dict.get("op_type") or op_dict.get("op") or op_dict.get("action")
    if isinstance(op_type, str):
        if op_type in ("AddConceptOp", "add_concept") and isinstance(op_dict.get("concept"), dict):
            c = dict(op_dict.pop("concept"))
            norm = {
                "op_type": "add_concept",
                "op_id": op_dict.get("op_id", f"op-{uuid.uuid4().hex[:8]}"),
            }
            norm.update(c)
            norm.update(op_dict)
            norm["op_type"] = "add_concept"
            op_dict, op_type = norm, "add_concept"

        if op_type.endswith("Op") and len(op_type) > 2:
            op_type = re.sub(r"(?<!^)(?=[A-Z])", "_", op_type[:-2]).lower()
        elif op_type.isupper():
            op_type = op_type.lower()
        if op_type in ("add_concept_attribute", "add_attribute"):
            op_type = "add_attribute"
        op_dict["op_type"] = op_type

        ent_schemes = (
            "urn:ontology:entity:",
            "urn:ontology:provisional:",
            "urn:ontology:event:",
            "urn:opendv:",
        )
        rel_schemes = (
            "urn:ontology:relationship:",
            "urn:ontology:provisional:",
            "urn:ontology:event:",
            "urn:opendv:",
        )

        if op_type == "add_concept":
            raw_uri = _normalize_urn(
                op_dict.get("concept_uri")
                or op_dict.get("concept_id")
                or op_dict.get("uri")
                or op_dict.get("id"),
                op_dict.get("concept_name"),
                "urn:ontology:entity:",
                ent_schemes,
            )
            if raw_uri:
                op_dict["concept_uri"] = raw_uri
            if not op_dict.get("concept_name"):
                op_dict["concept_name"] = (
                    op_dict.get("label")
                    or op_dict.get("name")
                    or (raw_uri.split(":")[-1].replace("_", " ").title() if raw_uri else "")
                )
        elif op_type == "add_relationship":
            raw_uri = _normalize_urn(
                op_dict.get("relationship_uri")
                or op_dict.get("relationship_id")
                or op_dict.get("uri")
                or op_dict.get("id"),
                op_dict.get("relationship_name"),
                "urn:ontology:relationship:",
                rel_schemes,
            )
            if raw_uri:
                op_dict["relationship_uri"] = raw_uri
            if not op_dict.get("relationship_name"):
                op_dict["relationship_name"] = (
                    op_dict.get("label")
                    or op_dict.get("name")
                    or (raw_uri.split(":")[-1].replace("_", " ").title() if raw_uri else "")
                )
        elif op_type == "add_attribute":
            raw_uri = _normalize_urn(
                op_dict.get("concept_uri") or op_dict.get("concept_id") or op_dict.get("concept"),
                None,
                "urn:ontology:entity:",
                ent_schemes,
            )
            if raw_uri:
                op_dict["concept_uri"] = raw_uri
            attr_spec = op_dict.get("attribute")
            if isinstance(attr_spec, dict):
                op_dict.setdefault(
                    "attribute_name", attr_spec.get("attribute_name") or attr_spec.get("name") or ""
                )
                dt_val = attr_spec.get("data_type") or attr_spec.get("datatype")
                if not op_dict.get("data_type") and dt_val:
                    op_dict["data_type"] = dt_val
                if not op_dict.get("description"):
                    op_dict["description"] = attr_spec.get("description") or attr_spec.get(
                        "definition"
                    )
                for k, v in attr_spec.items():
                    if k not in op_dict and k != "name":
                        op_dict[k] = v
            elif not op_dict.get("attribute_name"):
                op_dict["attribute_name"] = (
                    (attr_spec if isinstance(attr_spec, str) else None)
                    or op_dict.get("name")
                    or "attribute"
                )

        aliases = ["op", "action", "concept_id", "uri", "id", "label", "relationship_id", "concept"]
        if op_type != "add_reference_enumeration":
            aliases.append("name")
        if op_type != "add_attribute" or not isinstance(op_dict.get("attribute"), dict):
            aliases.append("attribute")
        for alias_key in aliases:
            op_dict.pop(alias_key, None)
    return op_dict


def normalize_external_mapping(m: dict[str, Any] | Any) -> Any:
    """Normalize heterogeneous external mapping dicts into ExternalMapping schema."""
    if not isinstance(m, dict):
        return m
    norm = dict(m)
    scheme = (
        norm.get("scheme")
        or norm.pop("source_system", None)
        or norm.pop("source_standard", None)
        or norm.pop("standard_name", None)
    )
    if scheme:
        norm["scheme"] = str(scheme)
    target = (
        norm.get("target_uri")
        or norm.pop("standard_uri", None)
        or norm.pop("external_id", None)
        or norm.pop("external_term", None)
    )
    if target:
        norm["target_uri"] = str(target)
    conf = norm.get("confidence")
    if conf is None:
        conf = norm.pop("confidence_score", None) or norm.pop("mapping_confidence", None)
    if conf is not None:
        with contextlib.suppress(ValueError, TypeError):
            norm["confidence"] = float(conf)
    pred = norm.get("predicate") or norm.pop("mapping_type", None)
    if pred:
        norm["predicate"] = str(pred)
    return norm


def normalize_delta_operation_typed(op: dict[str, Any] | Any) -> OntologyOperation:
    """Instantiate typed `OntologyOperation` from JSON dictionary."""
    if isinstance(op, BaseOntologyDeltaOp) or not isinstance(op, dict):
        return _OP_TYPE_ADAPTER.validate_python(op)
    op_copy = dict(op)
    raw_type = (
        op_copy.pop("op_type", None) or op_copy.pop("op", None) or op_copy.pop("operation", None)
    )
    if not raw_type:
        raise ValueError("Delta operation missing required 'op_type'")
    op_copy.pop("op", None)
    op_copy.pop("operation", None)

    op_type = str(raw_type)
    if op_type.endswith("Op") and len(op_type) > 2:
        op_type = op_type[:-2]
    op_type = re.sub(r"(?<!^)(?=[A-Z])", "_", op_type).lower()

    if not op_copy.get("op_id"):
        op_digest = hashlib.sha256(json.dumps(op_copy, sort_keys=True).encode("utf-8")).hexdigest()
        op_copy["op_id"] = f"op-{op_digest[:12]}"
    if isinstance(op_copy.get("external_mappings"), list):
        op_copy["external_mappings"] = [
            normalize_external_mapping(m) for m in op_copy["external_mappings"]
        ]

    if op_type == "add_concept":
        op_copy["op_type"] = "add_concept"
        if isinstance(op_copy.get("concept"), dict):
            c_dict = dict(op_copy.pop("concept"))
            if isinstance(c_dict.get("external_mappings"), list):
                c_dict["external_mappings"] = [
                    normalize_external_mapping(m) for m in c_dict["external_mappings"]
                ]
            norm: dict[str, Any] = {"op_type": "add_concept", "op_id": op_copy["op_id"]}
            norm.update(c_dict)
            norm.update(op_copy)
            norm["op_type"] = "add_concept"
            return _OP_TYPE_ADAPTER.validate_python(norm)
        return _OP_TYPE_ADAPTER.validate_python(op_copy)

    op_copy["op_type"] = op_type
    return _OP_TYPE_ADAPTER.validate_python(op_copy)


def load_session_version_ontology(
    sdir: Path,
    meta: SessionMetadata,
    version_str: str | None = None,
) -> GraphAOntology:
    """Load a committed GraphAOntology snapshot or synthesize genesis 0.0.0."""
    ver = (version_str or meta.head_version).lstrip("v")
    vdir = sdir / "versions" / normalize_version_tag(ver)
    ont_json = vdir / "ontology.json"
    if ont_json.is_file():
        try:
            return GraphAOntology.model_validate_json(ont_json.read_text(encoding="utf-8"))
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
            TypeError,
            ValueError,
        ) as exc:
            raise StudioWorkflowError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt ontology artifact for version 'v{ver}': {exc}",
                status_code=500,
                exit_code=1,
            ) from exc
    ont_yaml = vdir / "ontology.yaml"
    if ont_yaml.is_file():
        try:
            return GraphAOntology.model_validate(
                yaml.safe_load(ont_yaml.read_text(encoding="utf-8"))
            )
        except (
            OSError,
            UnicodeDecodeError,
            yaml.YAMLError,
            ValidationError,
            TypeError,
            ValueError,
        ) as exc:
            raise StudioWorkflowError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt ontology artifact for version 'v{ver}': {exc}",
                status_code=500,
                exit_code=1,
            ) from exc
    if ver == GENESIS_VERSION:
        return create_genesis_ontology(
            domain_namespace=sanitize_domain_namespace(meta.domain_namespace),
            enterprise_owner=meta.enterprise_owner or "Enterprise Architecture",
        )
    raise StudioWorkflowError(
        "CORRUPT_SESSION_STATE",
        f"Ontology artifact missing for version 'v{ver}' in session '{meta.session_id}'.",
        status_code=500,
        exit_code=1,
    )


def load_version_ontology_fail_closed(
    sdir: Path, meta: SessionMetadata, version_str: str
) -> GraphAOntology:
    """Load version ontology failing closed (CORRUPT_SESSION_RECORD vs non-ancestor 409)."""
    clean_ver = str(version_str).strip().lstrip("v") or GENESIS_VERSION
    if clean_ver != GENESIS_VERSION:
        vdir = sdir / "versions" / normalize_version_tag(clean_ver)
        if not vdir.is_dir():
            committed_tags = {
                normalize_version_tag(str(cv).strip().lstrip("v"))
                for cv in meta.committed_versions
                if str(cv).strip()
            }
            if normalize_version_tag(
                clean_ver
            ) in committed_tags or clean_ver == meta.head_version.lstrip("v"):
                raise StudioWorkflowError(
                    "CORRUPT_SESSION_RECORD",
                    f"Missing version directory 'v{clean_ver}' in session '{meta.session_id}'.",
                    status_code=500,
                    exit_code=1,
                )
            raise StudioWorkflowError(
                "PROPOSAL_STALE_REBASE_REQUIRED",
                f"Proposal base_version 'v{clean_ver}' does not exist in session history.",
                status_code=409,
                exit_code=6,
            )
    try:
        return load_session_version_ontology(sdir, meta, clean_ver)
    except (StudioWorkflowError, OSError, ValidationError, ValueError, yaml.YAMLError) as exc:
        raise StudioWorkflowError(
            "CORRUPT_SESSION_RECORD",
            f"Corrupt or missing ontology artifact for version 'v{clean_ver}' "
            f"in session '{meta.session_id}': {exc}",
            status_code=500,
            exit_code=1,
        ) from exc


def compute_expected_semver_for_ops(
    base_version: str,
    base_sha256: str,
    norm_ops: list[Any],
    base_ontology: GraphAOntology | None = None,
    domain_namespace: str | None = None,
) -> str:
    """Compute exact required target SemVer BEFORE full OntologyDelta instantiation."""
    clean_base = base_version.lstrip("v")
    if clean_base == GENESIS_VERSION:
        return derive_genesis_target_version(norm_ops)
    b_maj, b_min, b_pat = parse_semver_tuple(clean_base)
    validated_seed = OntologyDelta(
        delta_id=f"delta-probe-{uuid.uuid4().hex[:8]}",
        parent_sha256=base_sha256,
        base_version=clean_base,
        target_version=f"{b_maj + 1}.0.0",
        domain_namespace=domain_namespace,
        operations=norm_ops,
    )
    if not validated_seed.operations:
        return f"{b_maj}.{b_min}.{b_pat + 1}"
    probe = validated_seed.model_copy(update={"target_ontology_version": None})
    probe.__pydantic_fields_set__.discard("target_ontology_version")
    probe.__pydantic_fields_set__.discard("target_version")
    return compute_next_semver(clean_base, probe, base_ontology)


def compute_submitted_request_delta_sha256(
    *,
    delta_id: str | None,
    parent_sha256: str | None,
    base_sha256: str | None,
    base_version: str | None,
    target_version: str | None,
    domain_namespace: str | None,
    author: str | None,
    rationale: str | None,
    norm_ops: list[Any],
    fallback_base_version: str,
    fallback_parent_sha256: str,
    fallback_target_version: str,
    fallback_sha256: str,
) -> str:
    """Compute canonical SHA-256 of pre-normalization delta when delta_id is set."""
    if not delta_id:
        return fallback_sha256
    raw_parent = (
        parent_sha256
        if parent_sha256 is not None
        else (base_sha256 if base_sha256 is not None else fallback_parent_sha256)
    )
    try:
        return OntologyDelta(
            delta_id=delta_id,
            parent_sha256=raw_parent,
            base_version=(base_version or fallback_base_version).lstrip("v"),
            target_version=(target_version or fallback_target_version).lstrip("v"),
            domain_namespace=domain_namespace,
            operations=norm_ops,
            author=author,
            rationale=rationale,
        ).canonical_sha256()
    except (ValidationError, ValueError, TypeError):
        return fallback_sha256
