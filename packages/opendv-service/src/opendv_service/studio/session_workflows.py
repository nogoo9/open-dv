"""Shared Studio session creation, listing, and ontology import workflows."""

from __future__ import annotations

import contextlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from opendv_contracts.errors import OpenDVError
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.studio.delta_normalizer import StudioWorkflowError, raise_from_opendv_error
from opendv_service.studio.session_export_workflows import (
    clone_session_workflow,
    derive_session_status,
    export_bundle_workflow,
    export_session_ontology_workflow,
    import_bundle_sync,
    promote_session_workflow,
    resolve_target_promotion_contract_path,
    submit_feedback_workflow,
)
from opendv_service.studio.session_manager import (
    InvalidOntologyPayloadError,
    InvalidSessionIdentifierError,
    SessionAlreadyExistsError,
    SessionMetadata,
    StudioSessionManager,
)
from opendv_service.studio.session_source_workflows import (
    apply_starter_pack_workflow,
    persist_uploaded_source_sync,
    submit_clarification_answers_workflow,
)
from opendv_service.studio.workspace_security import (
    replace_with_retry,
    set_owner_only_permissions,
    validate_workspace_containment,
)

_DOMAIN_NAMESPACE_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def discover_workspace_ontology_file(ws_path: Path, domain_namespace: str) -> Path:
    """Resolve an ontology contract file from a workspace file or directory path."""
    if ws_path.is_file():
        return ws_path
    if not ws_path.is_dir():
        raise StudioWorkflowError("INVALID_WORKSPACE_PATH", "Specified workspace seed path is invalid or inaccessible.", status_code=400, exit_code=2)
    candidates = [
        ws_path / f"{domain_namespace}_graph_a_ontology.yaml",
        ws_path / "graph_a_ontology.yaml",
        ws_path / "graph_a.yaml",
        ws_path / "ontology.yaml",
        ws_path / "ontology.json",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    globbed = sorted(ws_path.glob("*_graph_a_ontology.yaml"))
    if globbed:
        return globbed[0]
    raise StudioWorkflowError("FILE_NOT_FOUND", f"No Graph A ontology contract found in workspace seed path '{ws_path.name}'.", status_code=404, exit_code=3)


def list_sessions_workflow(
    session_mgr: StudioSessionManager,
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    wizard_only: bool = False,
) -> list[dict[str, Any]] | dict[str, Any]:
    """List studio sessions with optional `wizard_only` D25 filtering + legacy count."""
    sessions = session_mgr.list_sessions()
    legacy_count = 0
    enriched: list[tuple[SessionMetadata, str, int]] = []
    for s in sessions:
        sdir = session_mgr.get_session_dir(s.session_id)
        eff_st = derive_session_status(sdir, s)
        ver = getattr(s, "wizard_schema_version", None)
        if ver is None:
            ver = 1
            sjson = sdir / "session.json"
            if sjson.is_file():
                with contextlib.suppress(OSError, json.JSONDecodeError):
                    raw = json.loads(sjson.read_text(encoding="utf-8"))
                    ver = int(raw.get("wizard_schema_version") or 1)
        if int(ver) < 2:
            legacy_count += 1
        enriched.append((s, eff_st, int(ver)))

    if wizard_only:
        enriched = [item for item in enriched if item[2] >= 2]
    if status:
        target_st = status.lower()
        enriched = [
            (s, eff_st, ver)
            for s, eff_st, ver in enriched
            if eff_st.lower() == target_st or s.status.lower() == target_st
        ]
    paginated = enriched[offset : offset + limit]
    items = [
        {
            "session_id": s.session_id,
            "domain_namespace": s.domain_namespace,
            "enterprise_owner": s.enterprise_owner,
            "status": eff_st,
            "active_version": s.head_version,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
            "wizard_schema_version": ver,
        }
        for s, eff_st, ver in paginated
    ]
    if wizard_only:
        return {"sessions": items, "legacy_session_count": legacy_count}
    return items


def build_session_summary_payload(sdir: Path, meta: SessionMetadata) -> dict[str, Any]:
    """Build standard StudioSessionDTO-compatible response payload."""
    return {
        "session_id": meta.session_id,
        "domain_namespace": meta.domain_namespace,
        "enterprise_owner": meta.enterprise_owner,
        "status": derive_session_status(sdir, meta),
        "active_version": meta.head_version,
        "head_sha256": meta.head_sha256,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
        "wizard_schema_version": int(getattr(meta, "wizard_schema_version", None) or 1),
    }


def _stamp_wizard_v2_if_needed(sdir: Path, meta: SessionMetadata) -> SessionMetadata:
    """Ensure newly created wizard sessions record `wizard_schema_version=2` (`D25`)."""
    if getattr(meta, "wizard_schema_version", None) == 2:
        return meta
    sjson = sdir / "session.json"
    if sjson.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            raw = json.loads(sjson.read_text(encoding="utf-8"))
            if raw.get("wizard_schema_version") != 2:
                raw["wizard_schema_version"] = 2
                tmp = sdir / f".session.json.{uuid.uuid4().hex[:8]}.tmp"
                tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
                set_owner_only_permissions(tmp)
                replace_with_retry(tmp, sjson)
                set_owner_only_permissions(sjson)
    return meta


def create_session_workflow(
    *,
    session_mgr: StudioSessionManager,
    allowed_roots: list[Path],
    session_id: str | None,
    domain_namespace: str,
    enterprise_owner: str,
    from_workspace: str | None,
    is_remote: bool,
) -> dict[str, Any]:
    if not _DOMAIN_NAMESPACE_PATTERN.match(domain_namespace):
        raise StudioWorkflowError("SYNTAX_INVALID_ARGUMENT", f"Invalid domain_namespace '{domain_namespace}'. Must match ^[a-z][a-z0-9_]{{0,63}}$", status_code=400, exit_code=2)

    initial_ontology: GraphAOntology | None = None
    if from_workspace:
        if is_remote:
            raise StudioWorkflowError("REMOTE_SERVER_PATH_FORBIDDEN", "Server-local 'from_workspace' paths are forbidden in remote_platform mode.", status_code=400, exit_code=7)
        raw_p = Path(from_workspace)
        cand_p = raw_p if raw_p.is_absolute() else (allowed_roots[0] / raw_p)
        try:
            validated_ws = validate_workspace_containment(cand_p, allowed_roots=allowed_roots, is_remote_request=False)
        except OpenDVError as exc:
            raise raise_from_opendv_error(exc) from exc
        if not validated_ws.exists():
            raise StudioWorkflowError("INVALID_WORKSPACE_PATH", "Specified workspace seed path does not exist within allowed workspace roots.", status_code=400, exit_code=2)
        ont_file = discover_workspace_ontology_file(validated_ws, domain_namespace)
        try:
            text = ont_file.read_text(encoding="utf-8")
            initial_ontology = (
                GraphAOntology.model_validate_json(text)
                if ont_file.suffix.lower() == ".json"
                else GraphAOntology.model_validate(yaml.safe_load(text))
            )
        except (ValidationError, ValueError, TypeError, yaml.YAMLError, OSError) as exc:
            raise StudioWorkflowError("INVALID_WORKSPACE_ONTOLOGY", f"Workspace ontology contract '{ont_file.name}' failed validation: {exc}", status_code=422, exit_code=4) from exc

    try:
        meta = session_mgr.create_session(
            session_id=session_id,
            initial_ontology=initial_ontology,
            domain_namespace=domain_namespace,
            enterprise_owner=enterprise_owner,
        )
    except SessionAlreadyExistsError as exc:
        raise StudioWorkflowError("SESSION_ALREADY_EXISTS", str(exc), status_code=409, exit_code=6) from exc
    except InvalidSessionIdentifierError as exc:
        raise StudioWorkflowError("INVALID_SESSION_IDENTIFIER", str(exc), status_code=400, exit_code=2) from exc
    except InvalidOntologyPayloadError as exc:
        raise StudioWorkflowError("INVALID_WORKSPACE_ONTOLOGY", str(exc), status_code=422, exit_code=4) from exc

    sdir = session_mgr.get_session_dir(meta.session_id)
    meta = _stamp_wizard_v2_if_needed(sdir, meta)
    return build_session_summary_payload(sdir, meta)


def import_ontology_session_workflow(
    *,
    session_mgr: StudioSessionManager,
    session_id: str | None,
    domain_namespace: str,
    enterprise_owner: str,
    import_format: str,
    content: str,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Import YAML/JSON/TTL ontology via Track B `ttl_importer` and verify JCS SHA-256."""
    from opendv_service.studio.ttl_importer import parse_ontology_import_payload

    if not _DOMAIN_NAMESPACE_PATTERN.match(domain_namespace):
        raise StudioWorkflowError("SYNTAX_INVALID_ARGUMENT", f"Invalid domain_namespace '{domain_namespace}'.", status_code=400, exit_code=2)
    try:
        ont = parse_ontology_import_payload(content, import_format=import_format, domain_namespace=domain_namespace)
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except (ValueError, ValidationError, yaml.YAMLError) as exc:
        raise StudioWorkflowError("INVALID_WORKSPACE_ONTOLOGY", f"Ontology import parse failed: {exc}", status_code=422, exit_code=4) from exc

    actual_sha = compute_ontology_sha256(ont)
    if expected_sha256 and expected_sha256.strip().lower() != actual_sha.lower():
        raise StudioWorkflowError(
            "PRECONDITION_BASE_SHA_MISMATCH",
            f"Imported ontology JCS SHA-256 '{actual_sha}' does not match expected '{expected_sha256}'.",
            status_code=412,
            exit_code=5,
        )

    try:
        meta = session_mgr.create_session(
            session_id=session_id,
            initial_ontology=ont,
            domain_namespace=domain_namespace,
            enterprise_owner=enterprise_owner,
        )
    except SessionAlreadyExistsError as exc:
        raise StudioWorkflowError("SESSION_ALREADY_EXISTS", str(exc), status_code=409, exit_code=6) from exc
    except InvalidSessionIdentifierError as exc:
        raise StudioWorkflowError("INVALID_SESSION_IDENTIFIER", str(exc), status_code=400, exit_code=2) from exc
    except InvalidOntologyPayloadError as exc:
        raise StudioWorkflowError("INVALID_WORKSPACE_ONTOLOGY", str(exc), status_code=422, exit_code=4) from exc

    sdir = session_mgr.get_session_dir(meta.session_id)
    meta = _stamp_wizard_v2_if_needed(sdir, meta)
    return build_session_summary_payload(sdir, meta)


def get_session_detail_workflow(session_mgr: StudioSessionManager, meta: SessionMetadata) -> dict[str, Any]:
    """Assemble full StudioSessionDetailDTO payload for a session."""
    from opendv_service.studio.atomic_commit import _load_grandfathered_concept_uris

    sdir = session_mgr.get_session_dir(meta.session_id)
    manifest_file = sdir / "inputs" / "manifest.json"
    sources_count = 0
    if manifest_file.is_file():
        with contextlib.suppress(Exception):
            mdata = json.loads(manifest_file.read_text(encoding="utf-8"))
            sources_count = sum(1 for f in mdata.get("files", []) if not f.get("tombstoned", False))
    proposals_count = len(list((sdir / "proposals").glob("*.json"))) if (sdir / "proposals").is_dir() else 0
    versions_count = len(list((sdir / "versions").glob("v*"))) if (sdir / "versions").is_dir() else 0
    return {
        "session_id": meta.session_id,
        "domain_namespace": meta.domain_namespace,
        "enterprise_owner": meta.enterprise_owner,
        "status": derive_session_status(sdir, meta),
        "active_version": meta.head_version,
        "head_sha256": meta.head_sha256,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
        "sources_count": sources_count,
        "proposals_count": proposals_count,
        "versions_count": versions_count,
        "grandfathered_concept_uris": sorted(_load_grandfathered_concept_uris(sdir)),
        "wizard_schema_version": int(getattr(meta, "wizard_schema_version", None) or 1),
    }


def get_session_manifest_workflow(
    session_mgr: StudioSessionManager,
    session_id: str,
    *,
    is_automated: bool,
) -> dict[str, Any]:
    """Read session inputs/manifest.json with confidential description redaction for automated actors."""
    mfile = session_mgr.get_session_dir(session_id) / "inputs" / "manifest.json"
    files: list[dict[str, Any]] = []
    if mfile.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            files = json.loads(mfile.read_text(encoding="utf-8")).get("files", [])
    if is_automated:
        files = [{**f, "description": None} if f.get("do_not_send_to_agent") else f for f in files]
    total_bytes = sum(f.get("size_bytes", 0) for f in files if not f.get("tombstoned", False))
    return {"session_id": session_id, "files": files, "total_bytes": total_bytes}


__all__ = [
    "apply_starter_pack_workflow",
    "clone_session_workflow",
    "create_session_workflow",
    "derive_session_status",
    "discover_workspace_ontology_file",
    "export_bundle_workflow",
    "export_session_ontology_workflow",
    "get_session_detail_workflow",
    "get_session_manifest_workflow",
    "import_bundle_sync",
    "import_ontology_session_workflow",
    "list_sessions_workflow",
    "persist_uploaded_source_sync",
    "promote_session_workflow",
    "resolve_target_promotion_contract_path",
    "submit_clarification_answers_workflow",
    "submit_feedback_workflow",
]
