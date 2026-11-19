"""Ontology Studio CLI session lifecycle & starter-pack command handlers."""

from __future__ import annotations

import argparse
import contextlib
import os
import secrets
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from opendv_cli.commands.ontology_studio_common import (
    _derive_session_status,
    _err,
    _err_from_opendv_error,
    _err_from_workflow_error,
    _get_session_manager,
    _get_studio_root,
    _require_session_context,
    _resolve_studio_principal,
    _validate_session,
)
from opendv_contracts.dtos.starter_packs import AssetManagementPackId
from opendv_contracts.errors import OpenDVError
from opendv_core.models.graph_a import GraphAOntology
from opendv_service.studio.delta_normalizer import StudioWorkflowError
from opendv_service.studio.session_manager import (
    CannotDeletePromotedSessionError,
    InvalidSessionIdentifierError,
    SessionAlreadyExistsError,
    SessionNotFoundError,
)
from opendv_service.studio.session_source_workflows import apply_starter_pack_workflow
from opendv_service.studio.session_workflows import discover_workspace_ontology_file
from opendv_service.studio.workspace_security import (
    WorkspaceSecurityManager,
    set_owner_only_permissions,
)
from opendv_sources.asset_management import AssetManagementStarterPackRegistry

if TYPE_CHECKING:
    from opendv_cli.executor import LocalExecutor


def handle_init(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """1. `opendv ontology studio init [--force] [--show-token]`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    _, role_tier = auth_res  # type: ignore[misc]
    if role_tier != "admin":
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", "Initializing or rotating local Studio secrets requires the 'admin' role.")
    studio_root = _get_studio_root(executor)
    secret_file = studio_root / ".odv_workspace_secret"
    token_file = studio_root / ".local_token"
    force = bool(getattr(args, "force", False))
    if secret_file.exists() and not force:
        return _err(6, "STUDIO_ALREADY_INITIALIZED", "Studio workspace already initialized. Use --force to rotate.")
    studio_root.mkdir(parents=True, exist_ok=True)
    set_owner_only_permissions(studio_root)
    if force:
        with contextlib.suppress(OSError):
            if secret_file.exists():
                secret_file.unlink()
    WorkspaceSecurityManager(studio_root=studio_root)
    token = secrets.token_hex(32)
    tmp_token = studio_root / f".token.{uuid.uuid4().hex[:8]}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(str(tmp_token), flags, 0o600)
    try:
        os.write(fd, token.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    set_owner_only_permissions(tmp_token)
    os.replace(tmp_token, token_file)
    set_owner_only_permissions(token_file)
    res_payload: dict[str, Any] = {"status": "initialized", "token_path": str(token_file), "principal": "human:local_admin", "studio_root": str(studio_root)}
    if getattr(args, "show_token", False):
        res_payload["local_token"] = token
    return 0, res_payload


def handle_create(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """2. `opendv ontology studio create`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    _principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "viewer":
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", "Read-only viewer role is not permitted to create Studio sessions.")
    sid = getattr(args, "session", None)
    if sid:
        err = _validate_session(sid)
        if err:
            return err
    raw_ns = getattr(args, "domain_namespace", None)
    domain_ns = "general" if (not raw_ns or (getattr(args, "_explicit_domain_namespace", None) is not True and raw_ns == "default")) else str(raw_ns)
    raw_owner = getattr(args, "enterprise_owner", None)
    owner = "Enterprise Architecture" if (not raw_owner or (getattr(args, "_explicit_enterprise_owner", None) is not True and raw_owner == "Enterprise")) else str(raw_owner)
    from_ws = getattr(args, "from_workspace", None)
    initial_ontology: GraphAOntology | None = None
    if from_ws:
        ws_path = Path(from_ws)
        if not ws_path.is_absolute():
            ws_path = ((executor.workspace_root if executor else Path.cwd()) / ws_path).resolve()
        if not ws_path.exists():
            return _err(2, "INVALID_WORKSPACE_PATH", f"Workspace directory not found: {from_ws}")
        found_yaml = ws_path if ws_path.is_file() else discover_workspace_ontology_file(ws_path, domain_ns)
        if not found_yaml:
            return _err(4, "INVALID_WORKSPACE_ONTOLOGY", f"No Graph A ontology contract found in '{from_ws}' (expected graph_a_ontology.yaml or *_graph_a_ontology.yaml)")
        try:
            initial_ontology = GraphAOntology.model_validate(yaml.safe_load(found_yaml.read_text(encoding="utf-8")))
        except (yaml.YAMLError, ValidationError, ValueError, OSError) as err_parse:
            return _err(4, "INVALID_WORKSPACE_ONTOLOGY", f"Failed to parse ontology in workspace: {err_parse}")
    session_mgr = _get_session_manager(executor)
    try:
        meta = session_mgr.create_session(session_id=sid, initial_ontology=initial_ontology, domain_namespace=domain_ns, enterprise_owner=owner)
    except SessionAlreadyExistsError as err_exists:
        return _err(6, "SESSION_ALREADY_EXISTS", str(err_exists))
    except InvalidSessionIdentifierError as err_id:
        return _err(2, "INVALID_SESSION_IDENTIFIER", str(err_id))
    sdir = session_mgr.get_session_dir(meta.session_id)
    return 0, {"session_id": meta.session_id, "domain_namespace": meta.domain_namespace, "enterprise_owner": meta.enterprise_owner, "status": _derive_session_status(sdir, meta), "active_version": meta.head_version, "head_sha256": meta.head_sha256, "created_at": meta.created_at, "updated_at": meta.updated_at, "wizard_schema_version": getattr(meta, "wizard_schema_version", 2)}


def handle_delete(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """3. `opendv ontology studio delete -s <session> [--force]`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    _, role_tier = auth_res  # type: ignore[misc]
    if role_tier != "admin":
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", "Deleting Studio sessions requires the 'admin' role.")
    sid = getattr(args, "session", None)
    err = _validate_session(sid)
    if err:
        return err
    session_mgr = _get_session_manager(executor)
    try:
        session_mgr.delete_session(str(sid), force=bool(getattr(args, "force", False)))
    except SessionNotFoundError:
        return _err(3, "SESSION_NOT_FOUND", f"Session '{sid}' not found")
    except CannotDeletePromotedSessionError as exc:
        return _err(6, "CANNOT_DELETE_PROMOTED_SESSION", str(exc))
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)
    return 0, {"session_id": str(sid), "status": "DELETED", "deleted": True}


def handle_list(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """4. `opendv ontology studio list`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    session_mgr = _get_session_manager(executor)
    enriched = [(s, _derive_session_status(session_mgr.get_session_dir(s.session_id), s)) for s in session_mgr.list_sessions()]
    status_filter = getattr(args, "status", None)
    if status_filter:
        target_st = str(status_filter).upper()
        enriched = [(s, eff_st) for s, eff_st in enriched if eff_st.upper() == target_st or s.status.upper() == target_st]
    offset = getattr(args, "offset", 0) or 0
    limit = getattr(args, "limit", 50) or 50
    return 0, [{"session_id": s.session_id, "domain_namespace": s.domain_namespace, "enterprise_owner": s.enterprise_owner, "status": eff_st, "active_version": s.head_version, "created_at": s.created_at, "updated_at": s.updated_at, "wizard_schema_version": getattr(s, "wizard_schema_version", 2)} for s, eff_st in enriched[offset : offset + limit]]


def handle_status(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """5. `opendv ontology studio status -s <session>`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    _, meta, sdir, _ = ctx  # type: ignore[misc]
    return 0, {"session_id": meta.session_id, "status": _derive_session_status(sdir, meta), "active_version": meta.head_version, "head_sha256": meta.head_sha256, "taint_classification": meta.taint_classification, "active_jobs": 0}


def handle_starter_pack_list(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """List available Asset Management starter packs."""
    _ = (args, executor)
    return 0, [p.model_dump(mode="json") for p in AssetManagementStarterPackRegistry.list_packs()]


def handle_starter_pack_apply(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """Apply a vertical starter pack to the active studio session."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "viewer":
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", "Read-only viewer cannot apply starter packs.")
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, meta, sdir, sid = ctx  # type: ignore[misc]
    pack_id_str = getattr(args, "pack", None)
    if not pack_id_str:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --pack flag")
    try:
        pack_id = AssetManagementPackId(pack_id_str)
    except ValueError:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Invalid starter pack id: {pack_id_str}")
    pack = AssetManagementStarterPackRegistry.load_pack(pack_id)
    actor_name = principal if isinstance(principal, str) else getattr(principal, "principal", "human:steward")
    try:
        _ver, new_sha, c_count, r_count = apply_starter_pack_workflow(session_mgr=session_mgr, meta=meta, sdir=sdir, pack_ids=[pack_id], base_version=None, domain_namespace=None, expected_sha=None, principal_id=actor_name)
    except StudioWorkflowError as exc:
        return _err_from_workflow_error(exc)
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)
    return 0, {"pack_id": pack_id.value, "session_id": sid, "applied": True, "concepts_added": c_count, "relationships_added": r_count, "reference_enums_added": len(getattr(pack, "reference_enumerations", [])), "new_head_sha256": new_sha}
