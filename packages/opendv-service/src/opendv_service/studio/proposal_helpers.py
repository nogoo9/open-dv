"""Shared Studio proposal normalization, file I/O, and SHA-256 lineage helpers."""

from __future__ import annotations

import contextlib
import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from opendv_contracts.dtos.ontology import OntologyDelta
from opendv_contracts.errors import OpenDVError
from opendv_core.genesis import GENESIS_VERSION
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.studio.delta_normalizer import (
    StudioWorkflowError,
    compute_expected_semver_for_ops,
    load_session_version_ontology,
    normalize_delta_operation_wire,
    raise_from_opendv_error,
    resolve_session_version_dir_safe,
    validate_version_syntax,
)
from opendv_service.studio.session_manager import SessionMetadata
from opendv_service.studio.workspace_security import (
    WINDOWS_RESERVED_DEVICES,
    replace_with_retry,
    set_owner_only_permissions,
)

_PROPOSAL_ID_PATTERN: re.Pattern[str] = re.compile(r"\A[a-zA-Z0-9_-]{1,64}\Z")


def resolve_proposal_file_safe(sdir: Path, proposal_id: str) -> Path:
    """Validate proposal_id syntax and resolve its file strictly under proposals/."""
    if (
        not isinstance(proposal_id, str)
        or not _PROPOSAL_ID_PATTERN.match(proposal_id)
        or proposal_id.lower() in WINDOWS_RESERVED_DEVICES
    ):
        raise StudioWorkflowError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid proposal identifier '{proposal_id}'.",
            status_code=400,
            exit_code=2,
        )
    props_real = os.path.realpath(sdir / "proposals")
    props_pfx = props_real if props_real.endswith(os.sep) else props_real + os.sep
    target_real = os.path.realpath(os.path.join(props_real, f"{proposal_id}.json"))
    if not target_real.startswith(props_pfx):
        raise StudioWorkflowError(
            "INVALID_WORKSPACE_PATH",
            f"Path traversal detected in proposal identifier '{proposal_id}'.",
            status_code=400,
            exit_code=2,
        )
    return Path(target_real)


def require_proposal_file(sdir: Path, proposal_id: str) -> Path:
    """Resolve proposal path and raise PROPOSAL_NOT_FOUND if absent."""
    prop_file = resolve_proposal_file_safe(sdir, proposal_id)
    if not prop_file.is_file():
        raise StudioWorkflowError(
            "PROPOSAL_NOT_FOUND",
            f"Proposal '{proposal_id}' not found.",
            status_code=404,
            exit_code=3,
        )
    return prop_file


def read_pending_proposal(
    prop_file: Path, session_id: str, proposal_id: str
) -> dict[str, Any]:
    """Read, normalize, and verify a proposal is still PENDING."""
    prop_data = normalize_proposal_record(
        read_proposal_json_safe(prop_file, proposal_id),
        session_id=session_id,
        fallback_proposal_id=proposal_id,
    )
    if prop_data.get("status") in ("ACCEPTED", "REJECTED", "SUPERSEDED"):
        raise StudioWorkflowError(
            "PROPOSAL_ALREADY_RESOLVED",
            f"Proposal '{proposal_id}' has already been resolved ({prop_data.get('status')}).",
            status_code=409,
            exit_code=6,
        )
    return prop_data


def commit_delta_workflow_result(
    *,
    session_mgr: Any,
    sdir: Path,
    meta: SessionMetadata,
    delta: OntologyDelta,
    base_ontology: GraphAOntology,
    principal_id: str,
    principal_role: str,
    if_match: str | None,
    waivers: list[Any] | None,
    proposal_id: str | None = None,
    parent_sha_override: str | None = None,
    evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute AtomicCommitService.commit_delta and return canonical commit summary."""
    from opendv_service.studio.atomic_commit import (
        AtomicCommitService,
        ConcurrentModificationError,
        _load_grandfathered_concept_uris,
    )

    atomic_svc = AtomicCommitService(session_manager=session_mgr)
    try:
        commit_res = atomic_svc.commit_delta(
            session_id=sdir.name,
            delta=delta,
            proposal_id=proposal_id,
            author=principal_id,
            actor_role=principal_role,
            if_match=if_match,
            evaluation=evaluation,
            waivers=waivers or None,
            grandfathered_concept_uris=_load_grandfathered_concept_uris(sdir),
        )
    except ConcurrentModificationError as exc:
        if proposal_id is not None and "already been resolved" in str(exc):
            raise StudioWorkflowError(
                "PROPOSAL_ALREADY_RESOLVED",
                f"Proposal '{proposal_id}' has already been resolved.",
                status_code=409,
                exit_code=6,
            ) from exc
        raise StudioWorkflowError(
            "ONT_EVO_CONCURRENT_MODIFICATION", str(exc), status_code=412, exit_code=5
        ) from exc
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc

    return {
        "session_id": meta.session_id,
        "target_version": commit_res.version.lstrip("v"),
        "target_sha256": commit_res.target_sha256,
        "parent_sha256": (
            parent_sha_override if parent_sha_override is not None else meta.head_sha256
        ),
        "delta_id": delta.delta_id,
        "bump_type": delta.highest_semver_class(base_ontology),
        "applied_operations_count": len(delta.operations),
    }


def read_proposal_json_safe(prop_file: Path, proposal_id: str) -> dict[str, Any]:
    """Read and validate a proposal JSON file."""
    if not prop_file.is_file():
        raise StudioWorkflowError(
            "PROPOSAL_NOT_FOUND",
            f"Proposal '{proposal_id}' not found.",
            status_code=404,
            exit_code=3,
        )
    try:
        raw_data = json.loads(prop_file.read_text(encoding="utf-8"))
        if not isinstance(raw_data, dict):
            raise StudioWorkflowError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt proposal record '{proposal_id}': expected JSON object.",
                status_code=500,
                exit_code=1,
            )
        return raw_data
    except StudioWorkflowError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise StudioWorkflowError(
            "CORRUPT_SESSION_RECORD",
            f"Corrupt proposal record '{proposal_id}': {exc}",
            status_code=500,
            exit_code=1,
        ) from exc


def write_proposal_json_atomic(prop_file: Path, data: dict[str, Any]) -> None:
    """Atomically write proposal JSON with owner-only permissions."""
    prop_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_prop = prop_file.parent / f".{prop_file.stem}.{uuid.uuid4().hex[:8]}.tmp"
    tmp_prop.write_text(json.dumps(data, indent=2), encoding="utf-8")
    replace_with_retry(tmp_prop, prop_file)
    set_owner_only_permissions(prop_file)


def normalize_proposal_record(
    raw: dict[str, Any],
    session_id: str,
    fallback_proposal_id: str | None = None,
) -> dict[str, Any]:
    """Normalize flat ProposalRecord dicts and nested OntologyProposal dicts seamlessly."""
    delta_obj = raw.get("delta")
    delta_dict = delta_obj if isinstance(delta_obj, dict) else {}
    proposal_id = str(raw.get("proposal_id") or fallback_proposal_id or "")
    raw_base_ver = (
        raw.get("base_version")
        or raw.get("base_ontology_version")
        or delta_dict.get("base_ontology_version")
        or delta_dict.get("base_version")
        or GENESIS_VERSION
    )
    base_version = str(raw_base_ver).lstrip("v") or GENESIS_VERSION
    raw_base_sha = (
        raw.get("base_sha256")
        if raw.get("base_sha256") not in (None, "")
        else (
            raw.get("base_ontology_sha256")
            if raw.get("base_ontology_sha256") not in (None, "")
            else (delta_dict.get("parent_sha256") or delta_dict.get("base_sha256") or "")
        )
    )
    raw_status = raw.get("status")
    status = (
        str(raw_status).upper() if isinstance(raw_status, str) and raw_status.strip() else "PENDING"
    )
    if isinstance(raw.get("operations"), list):
        operations = list(raw["operations"])
    elif isinstance(delta_dict.get("operations"), list):
        operations = list(delta_dict["operations"])
    else:
        operations = []
    proposer = str(
        raw.get("proposer")
        or delta_dict.get("author")
        or raw.get("recommender_id")
        or "agent:unknown"
    )
    created_at = str(
        raw.get("created_at")
        or raw.get("created_at_utc")
        or delta_dict.get("created_at")
        or datetime.now(UTC).isoformat()
    )
    rationale_val = (
        raw.get("rationale") if raw.get("rationale") is not None else delta_dict.get("rationale")
    )
    normalized = dict(raw)
    normalized.update(
        {
            "proposal_id": proposal_id,
            "session_id": session_id,
            "base_version": base_version,
            "base_sha256": str(raw_base_sha),
            "status": status,
            "operations": operations,
            "proposer": proposer,
            "created_at": created_at,
            "rationale": str(rationale_val) if rationale_val is not None else None,
        }
    )
    return normalized


def ensure_session_version_exists(sdir: Path, base_version: str | None) -> None:
    """Validate version parameter syntax and confirm `sdir / 'versions' / vX.Y.Z` exists."""
    if base_version is None:
        return
    validate_version_syntax(base_version)
    vdir = resolve_session_version_dir_safe(sdir, base_version)
    if not vdir.is_dir():
        raise StudioWorkflowError(
            "VERSION_NOT_FOUND",
            f"Version '{base_version}' not found in session.",
            status_code=404,
            exit_code=3,
        )


def compute_stored_proposal_delta_sha256(
    sdir: Path,
    meta: SessionMetadata,
    raw_prop: dict[str, Any],
    proposal_id: str,
) -> str:
    """Compute canonical RFC 8785 SHA-256 of a stored proposal's effective delta."""
    prop_data = normalize_proposal_record(
        raw_prop, session_id=meta.session_id, fallback_proposal_id=proposal_id
    )
    ops = [normalize_delta_operation_wire(op) for op in prop_data.get("operations", [])]
    prop_base_ver = (
        str(prop_data.get("base_version") or GENESIS_VERSION).lstrip("v") or GENESIS_VERSION
    )
    prop_base_sha = str(prop_data.get("base_sha256") or "")
    base_ont = load_session_version_ontology(sdir, meta, prop_base_ver)
    eff_parent_sha = prop_base_sha or compute_ontology_sha256(base_ont)
    try:
        raw_delta = raw_prop.get("delta")
        if isinstance(raw_delta, dict):
            if "operations" not in raw_prop:
                return OntologyDelta.model_validate(raw_delta).canonical_sha256()
            cleaned_delta = {
                k: v
                for k, v in raw_delta.items()
                if k not in ("base_sha256", "base_version", "target_version")
            }
            t_ver = compute_expected_semver_for_ops(
                base_version=prop_base_ver,
                base_sha256=eff_parent_sha,
                norm_ops=ops,
                base_ontology=base_ont,
                domain_namespace=cleaned_delta.get("domain_namespace") or meta.domain_namespace,
            )
            cleaned_delta.update(
                {
                    "parent_sha256": ""
                    if prop_base_sha == "" and raw_delta.get("parent_sha256") == ""
                    else eff_parent_sha,
                    "base_ontology_version": prop_base_ver,
                    "target_ontology_version": t_ver,
                    "operations": ops,
                }
            )
            return OntologyDelta.model_validate(cleaned_delta).canonical_sha256()
        t_ver = compute_expected_semver_for_ops(
            base_version=prop_base_ver,
            base_sha256=eff_parent_sha,
            norm_ops=ops,
            base_ontology=base_ont,
            domain_namespace=meta.domain_namespace,
        )
        return OntologyDelta(
            delta_id=str(raw_prop.get("delta_id") or f"delta-prop-{prop_data['proposal_id']}"),
            parent_sha256=eff_parent_sha,
            base_version=prop_base_ver,
            target_version=t_ver,
            domain_namespace=meta.domain_namespace,
            operations=ops,
            author=prop_data.get("proposer"),
            rationale=prop_data.get("rationale"),
        ).canonical_sha256()
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except (ValidationError, ValueError, TypeError) as exc:
        raise StudioWorkflowError(
            "CORRUPT_SESSION_RECORD",
            f"Corrupt proposal delta in proposal '{proposal_id}': {exc}",
            status_code=500,
            exit_code=1,
        ) from exc


def resolve_proposal_base_version_and_sha(
    sdir: Path,
    meta: SessionMetadata,
    req_base_version: str | None,
    req_base_sha256: str | None,
) -> tuple[str, str, GraphAOntology]:
    """Resolve exact proposal base_version, JCS SHA-256, and base ontology."""
    clean_req_sha: str | None = None
    if req_base_sha256 is not None:
        s = req_base_sha256.strip()
        clean_req_sha = s[1:-1] if s.startswith('"') and s.endswith('"') and len(s) == 66 else s

    head_ver = meta.head_version.lstrip("v")
    if req_base_version is not None:
        ensure_session_version_exists(sdir, req_base_version)
        base_version = req_base_version.lstrip("v")
        base_ont = load_session_version_ontology(sdir, meta, base_version)
        version_sha256 = (
            meta.head_sha256 if base_version == head_ver else compute_ontology_sha256(base_ont)
        )
        if (
            clean_req_sha is not None
            and not (clean_req_sha == "" and base_version == GENESIS_VERSION)
            and clean_req_sha != version_sha256
        ):
            raise StudioWorkflowError(
                "PRECONDITION_BASE_SHA_MISMATCH",
                f"Provided base_sha256 ('{req_base_sha256}') does not match "
                f"version 'v{base_version}' SHA-256 ('{version_sha256}').",
                status_code=400,
                exit_code=5,
            )
        return base_version, version_sha256, base_ont

    if (
        clean_req_sha is None
        or clean_req_sha == meta.head_sha256
        or (clean_req_sha == "" and head_ver == GENESIS_VERSION)
    ):
        return head_ver, meta.head_sha256, load_session_version_ontology(sdir, meta, head_ver)

    versions_dir = sdir / "versions"
    if versions_dir.is_dir():
        for vdir in sorted(versions_dir.iterdir()):
            if not vdir.is_dir():
                continue
            cand_ver = vdir.name.lstrip("v")
            with contextlib.suppress(StudioWorkflowError):
                cand_ont = load_session_version_ontology(sdir, meta, cand_ver)
                cand_sha = compute_ontology_sha256(cand_ont)
                if cand_sha == clean_req_sha:
                    return cand_ver, cand_sha, cand_ont

    raise StudioWorkflowError(
        "PRECONDITION_BASE_SHA_MISMATCH",
        f"Provided base_sha256 ('{req_base_sha256}') does not match active head "
        f"('{meta.head_sha256}') or any committed session version.",
        status_code=400,
        exit_code=5,
    )
