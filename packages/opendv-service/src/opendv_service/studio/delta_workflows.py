"""Shared Studio ontology delta validation and governed commit workflows."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from opendv_contracts.dtos.ontology import (
    DESTRUCTIVE_OP_TYPES,
    OntologyDelta,
    OntologyValidationResult,
    is_destructive_delta,
)
from opendv_contracts.errors import OpenDVError
from opendv_core.genesis import GENESIS_VERSION
from opendv_service.ontology.applier import apply as apply_delta
from opendv_service.ontology.validator import (
    GovernanceValidator,
    build_workspace_deployment_provider,
    validate_ontology_delta,
)
from opendv_service.studio.atomic_commit import (
    _load_grandfathered_concept_uris,
    _LocalWaiverVerifier,
)
from opendv_service.studio.delta_normalizer import (
    StudioWorkflowError,
    compute_expected_semver_for_ops,
    compute_submitted_request_delta_sha256,
    derive_genesis_target_version,
    load_session_version_ontology,
    load_version_ontology_fail_closed,
    normalize_delta_operation_typed,
    normalize_delta_operation_wire,
    normalize_external_mapping,
    parse_strong_etag_or_error,
    raise_from_opendv_error,
    validate_version_syntax,
)
from opendv_service.studio.proposal_helpers import commit_delta_workflow_result
from opendv_service.studio.session_manager import SessionMetadata, StudioSessionManager
from opendv_service.studio.waiver_workflows import build_and_sign_commit_waivers


def validate_session_delta_workflow(
    *,
    session_mgr: StudioSessionManager,
    meta: SessionMetadata,
    sdir: Path,
    payload_dict: dict[str, Any],
    principal_id: str,
    is_admin: bool,
) -> OntologyValidationResult:
    """Dry-run two-phase structural and governance validation on an OntologyDelta."""
    violations: list[str] = []
    diagnostics: list[str] = []
    primary_error_code: str | None = None
    norm_ops = [normalize_delta_operation_wire(op) for op in payload_dict.get("operations", [])]

    eff_parent_raw = (
        payload_dict.get("parent_sha256")
        if payload_dict.get("parent_sha256") is not None
        else payload_dict.get("base_sha256")
    )
    eff_base_ver = payload_dict.get("base_version") or meta.head_version
    req_target_ver = payload_dict.get("target_version")

    if meta.head_version == GENESIS_VERSION:
        if eff_parent_raw not in ("", None, meta.head_sha256):
            violations.append(
                "Genesis delta parent_sha256 must be empty or match genesis digest, "
                f"got '{eff_parent_raw}'."
            )
            primary_error_code = primary_error_code or "ONT_EVO_CONCURRENT_MODIFICATION"
        expected_genesis = derive_genesis_target_version(norm_ops)
        if req_target_ver and str(req_target_ver).lstrip("v") != expected_genesis:
            violations.append(
                f"target_version '{req_target_ver}' does not match "
                f"expected genesis version '{expected_genesis}'."
            )
            primary_error_code = primary_error_code or "ONT_EVO_CONTRACT_VIOLATION"
    else:
        clean_parent = str(eff_parent_raw or "").strip()
        if clean_parent != meta.head_sha256:
            violations.append(
                f"parent_sha256 '{clean_parent}' does not match session head '{meta.head_sha256}'."
            )
            primary_error_code = primary_error_code or "ONT_EVO_CONCURRENT_MODIFICATION"
        if str(eff_base_ver).lstrip("v") != meta.head_version.lstrip("v"):
            violations.append(
                f"base_version '{eff_base_ver}' does not match "
                f"session head version '{meta.head_version}'."
            )
            primary_error_code = primary_error_code or "ONT_EVO_CONCURRENT_MODIFICATION"

    base_ontology = load_session_version_ontology(sdir, meta, meta.head_version)
    delta: OntologyDelta | None = None
    try:
        expected_target = compute_expected_semver_for_ops(
            base_version=meta.head_version,
            base_sha256=meta.head_sha256,
            norm_ops=norm_ops,
            base_ontology=base_ontology,
            domain_namespace=meta.domain_namespace,
        )
        if req_target_ver and str(req_target_ver).lstrip("v") != expected_target.lstrip("v"):
            violations.append(
                f"target_version '{req_target_ver}' does not match "
                f"required semver bump '{expected_target}'."
            )
            primary_error_code = primary_error_code or "ONT_EVO_CONTRACT_VIOLATION"
        delta = OntologyDelta(
            delta_id=payload_dict.get("delta_id") or f"delta-val-{uuid.uuid4().hex[:8]}",
            parent_sha256=meta.head_sha256,
            base_version=meta.head_version,
            target_version=req_target_ver or expected_target,
            domain_namespace=meta.domain_namespace,
            operations=norm_ops,
            author=payload_dict.get("author") or "studio_validator",
            rationale=payload_dict.get("rationale"),
        )
    except OpenDVError as exc:
        violations.append(f"Delta structural validation failure: {exc}")
        raw_ec = getattr(exc, "error_code", None)
        primary_error_code = primary_error_code or (
            raw_ec.value
            if hasattr(raw_ec, "value")
            else str(raw_ec or "ONT_EVO_CONTRACT_VIOLATION")
        )
    except (ValidationError, ValueError, TypeError) as exc:
        violations.append(f"Delta structural validation failure: {exc}")
        primary_error_code = primary_error_code or "ONT_EVO_CONTRACT_VIOLATION"

    if delta is not None and not violations:
        existing_concepts = {c.concept_uri for c in base_ontology.canonical_business_concepts}
        for op in delta.operations:
            if getattr(op, "op_type", None) == "add_concept":
                c_uri = getattr(op, "concept_uri", None)
                if c_uri and c_uri in existing_concepts:
                    violations.append(
                        f"Duplicate concept conflict: '{c_uri}' already exists in base ontology."
                    )
                    primary_error_code = primary_error_code or "ONT_EVO_CONTRACT_VIOLATION"
        evo_res = validate_ontology_delta(base_ontology, delta)
        if not evo_res.valid:
            violations.extend(evo_res.violations)
            primary_error_code = primary_error_code or (
                evo_res.error_code or "ONT_EVO_CONTRACT_VIOLATION"
            )
        diagnostics.extend(evo_res.diagnostics)

    if delta is not None and not violations:
        try:
            target_ont = apply_delta(base_ontology, delta, validate_structural=True)
            dep_prov = build_workspace_deployment_provider(
                getattr(session_mgr, "deployment_workspace_root", None)
                or getattr(session_mgr, "workspace_root", None)
            )
            gv = GovernanceValidator(
                deployment_provider=dep_prov,
                waiver_verifier=_LocalWaiverVerifier(session_mgr.security_manager),
            )
            gv.validate_deployed_contracts(base_ontology, delta)
            g_rat = payload_dict.get("grounding_rationale")
            allow_inline = (
                bool(payload_dict.get("low_confidence_waiver"))
                and bool(g_rat and len(str(g_rat).strip()) >= 10)
                and is_admin
            )
            dry_sub_sha = compute_submitted_request_delta_sha256(
                delta_id=payload_dict.get("delta_id"),
                parent_sha256=payload_dict.get("parent_sha256"),
                base_sha256=payload_dict.get("base_sha256"),
                base_version=payload_dict.get("base_version"),
                target_version=payload_dict.get("target_version"),
                domain_namespace=payload_dict.get("domain_namespace"),
                author=payload_dict.get("author"),
                rationale=payload_dict.get("rationale"),
                norm_ops=norm_ops,
                fallback_base_version=meta.head_version,
                fallback_parent_sha256=meta.head_sha256,
                fallback_target_version=delta.target_version or meta.head_version,
                fallback_sha256=delta.canonical_sha256(),
            )
            dry_waivers = build_and_sign_commit_waivers(
                sdir=sdir,
                session_mgr=session_mgr,
                base_ontology=base_ontology,
                delta=delta,
                principal_id=principal_id,
                low_confidence_waiver=allow_inline,
                grounding_rationale=g_rat if allow_inline else None,
                explicit_ungrounded=payload_dict.get("ungrounded_concept")
                if allow_inline
                else None,
                proposal_id=None,
                submitted_delta_sha256=dry_sub_sha,
            )
            gv.validate_grounding(
                base_ontology,
                delta,
                target_ont,
                waivers=dry_waivers or None,
                grandfathered_concept_uris=_load_grandfathered_concept_uris(sdir),
            )
        except OpenDVError as gov_exc:
            violations.append(str(gov_exc))
            raw_ec = getattr(gov_exc, "error_code", None)
            primary_error_code = primary_error_code or (
                raw_ec.value
                if hasattr(raw_ec, "value")
                else str(raw_ec or "ONT_EVO_GROUNDING_VIOLATION")
            )
        except (ValidationError, ValueError) as exc:
            violations.append(f"Semantic conflict: {exc}")
            primary_error_code = primary_error_code or "ONT_EVO_CONTRACT_VIOLATION"

    is_valid = len(violations) == 0
    return OntologyValidationResult(
        valid=is_valid,
        violations=violations,
        diagnostics=diagnostics,
        error_code=None if is_valid else (primary_error_code or "ONT_EVO_CONTRACT_VIOLATION"),
    )


def apply_session_delta_workflow(
    *,
    session_mgr: StudioSessionManager,
    meta: SessionMetadata,
    sdir: Path,
    payload_dict: dict[str, Any],
    if_match: str | None,
    principal_id: str,
    principal_role: str,
    is_admin: bool,
) -> dict[str, Any]:
    """Direct governed delta commit with strict OCC If-Match precondition."""
    if not if_match:
        raise StudioWorkflowError(
            "MISSING_PRECONDITION_HEADER",
            "Applying a direct delta requires an 'If-Match' header anchoring session head SHA-256.",
            status_code=400,
            exit_code=2,
        )
    clean_if_match = parse_strong_etag_or_error(if_match)
    norm_ops = [normalize_delta_operation_wire(op) for op in payload_dict.get("operations", [])]
    low_waiver = bool(payload_dict.get("low_confidence_waiver", False))
    if not norm_ops:
        if not is_admin and low_waiver:
            raise StudioWorkflowError(
                "FORBIDDEN_ADMIN_REQUIRED",
                "Administrative grounding waivers strictly require administrator role.",
                status_code=403,
                exit_code=7,
            )
        raise StudioWorkflowError(
            "NO_OP_DELTA",
            "Direct delta commit requires at least one semantic operation.",
            status_code=400,
            exit_code=1,
        )

    req_base_ver = payload_dict.get("base_version")
    if req_base_ver is not None:
        validate_version_syntax(req_base_ver)

    base_ontology = load_session_version_ontology(sdir, meta, meta.head_version)
    eff_delta_id = payload_dict.get("delta_id") or f"delta-{uuid.uuid4().hex[:12]}"
    eff_base_version = str(req_base_ver or meta.head_version).lstrip("v")

    has_destructive_raw = any(
        (op.get("op_type") if isinstance(op, dict) else getattr(op, "op_type", None))
        in DESTRUCTIVE_OP_TYPES
        for op in norm_ops
    )
    if (low_waiver or has_destructive_raw) and not is_admin:
        raise StudioWorkflowError(
            "FORBIDDEN_ADMIN_REQUIRED",
            "Destructive (MAJOR) ontology revisions and grounding waivers "
            "require administrator role.",
            status_code=403,
            exit_code=7,
        )

    raw_body_parent = (
        payload_dict.get("parent_sha256")
        if payload_dict.get("parent_sha256") is not None
        else payload_dict.get("base_sha256")
    )
    active_ver_clean = meta.head_version.lstrip("v")
    if eff_base_version != active_ver_clean:
        raise StudioWorkflowError(
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"Delta base_version '{req_base_ver}' does not match "
            f"active head version '{meta.head_version}'.",
            status_code=412,
            exit_code=5,
        )
    if active_ver_clean != GENESIS_VERSION and raw_body_parent == "":
        raise StudioWorkflowError(
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"Empty genesis base_sha256 cannot be committed against head '{meta.head_version}'.",
            status_code=412,
            exit_code=5,
        )

    is_genesis_empty = active_ver_clean == GENESIS_VERSION and raw_body_parent in ("", None)
    clean_body_parent: str | None = None
    if not is_genesis_empty and raw_body_parent is not None:
        clean_body_parent = parse_strong_etag_or_error(
            str(raw_body_parent), header_name="parent_sha256"
        )
        if clean_if_match != clean_body_parent:
            raise StudioWorkflowError(
                "PRECONDITION_BASE_SHA_MISMATCH",
                f"If-Match header ('{clean_if_match}') does not match "
                f"body parent_sha256 ('{clean_body_parent}').",
                status_code=412,
                exit_code=5,
            )
    if meta.head_sha256 != clean_if_match:
        raise StudioWorkflowError(
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"Session head SHA-256 ('{meta.head_sha256}') does not match "
            f"expected ('{clean_if_match}').",
            status_code=412,
            exit_code=5,
        )
    if (
        active_ver_clean != GENESIS_VERSION
        and clean_body_parent is not None
        and clean_body_parent != meta.head_sha256
    ):
        raise StudioWorkflowError(
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"Delta base_sha256 ('{clean_body_parent}') does not match "
            f"session head SHA-256 ('{meta.head_sha256}').",
            status_code=412,
            exit_code=5,
        )

    try:
        expected_semver = compute_expected_semver_for_ops(
            meta.head_version, meta.head_sha256, norm_ops, base_ontology, meta.domain_namespace
        )
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except (ValidationError, ValueError, TypeError) as exc:
        raise StudioWorkflowError(
            "ONT_EVO_CONTRACT_VIOLATION",
            f"Delta structural validation failure: {exc}",
            status_code=422,
            exit_code=4,
        ) from exc

    req_target_ver = payload_dict.get("target_version")
    if req_target_ver and str(req_target_ver).lstrip("v") != expected_semver.lstrip("v"):
        raise StudioWorkflowError(
            "ONT_EVO_CONTRACT_VIOLATION",
            f"Target version '{req_target_ver}' does not match required "
            f"ONT-EVO-001 SemVer bump '{expected_semver}'.",
            status_code=422,
            exit_code=4,
        )

    try:
        delta = OntologyDelta(
            delta_id=eff_delta_id,
            parent_sha256=meta.head_sha256,
            base_version=meta.head_version,
            target_version=expected_semver,
            domain_namespace=meta.domain_namespace,
            operations=norm_ops,
            author=principal_id,
            rationale=payload_dict.get("rationale") or payload_dict.get("grounding_rationale"),
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise StudioWorkflowError(
            "ONT_EVO_CONTRACT_VIOLATION",
            f"Delta structural validation failure: {exc}",
            status_code=422,
            exit_code=4,
        ) from exc

    if (is_destructive_delta(delta, base_ontology) or low_waiver) and not is_admin:
        raise StudioWorkflowError(
            "FORBIDDEN_ADMIN_REQUIRED",
            "Destructive (MAJOR) ontology revisions and grounding waivers "
            "require administrator role.",
            status_code=403,
            exit_code=7,
        )

    submitted_delta_sha = compute_submitted_request_delta_sha256(
        delta_id=payload_dict.get("delta_id"),
        parent_sha256=payload_dict.get("parent_sha256"),
        base_sha256=payload_dict.get("base_sha256"),
        base_version=payload_dict.get("base_version"),
        target_version=payload_dict.get("target_version"),
        domain_namespace=payload_dict.get("domain_namespace"),
        author=payload_dict.get("author"),
        rationale=payload_dict.get("rationale"),
        norm_ops=norm_ops,
        fallback_base_version=meta.head_version,
        fallback_parent_sha256=meta.head_sha256,
        fallback_target_version=expected_semver,
        fallback_sha256=delta.canonical_sha256(),
    )
    signed_waivers = build_and_sign_commit_waivers(
        sdir=sdir,
        session_mgr=session_mgr,
        base_ontology=base_ontology,
        delta=delta,
        principal_id=principal_id,
        low_confidence_waiver=low_waiver,
        grounding_rationale=payload_dict.get("grounding_rationale"),
        explicit_ungrounded=payload_dict.get("ungrounded_concept"),
        proposal_id=None,
        submitted_delta_sha256=submitted_delta_sha,
    )

    p_sha = raw_body_parent if raw_body_parent is not None else meta.head_sha256
    return commit_delta_workflow_result(
        session_mgr=session_mgr, sdir=sdir, meta=meta, delta=delta,
        base_ontology=base_ontology, principal_id=principal_id,
        principal_role=principal_role, if_match=clean_if_match,
        waivers=signed_waivers, parent_sha_override=p_sha,
    )


__all__ = [
    "StudioWorkflowError",
    "apply_session_delta_workflow",
    "compute_expected_semver_for_ops",
    "compute_submitted_request_delta_sha256",
    "derive_genesis_target_version",
    "load_session_version_ontology",
    "load_version_ontology_fail_closed",
    "normalize_delta_operation_typed",
    "normalize_delta_operation_wire",
    "normalize_external_mapping",
    "parse_strong_etag_or_error",
    "raise_from_opendv_error",
    "validate_session_delta_workflow",
    "validate_version_syntax",
]
