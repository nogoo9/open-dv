"""Ontology Studio CLI export, workspace promote, and bundle import/export/clone handlers."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from opendv_cli.commands.ontology_studio_common import (
    _VERSION_PATTERN,
    MIN_PROMOTION_RATIONALE_LENGTH,
    _atomic_write_text,
    _err,
    _err_from_opendv_error,
    _get_session_manager,
    _get_studio_root,
    _require_session,
    _require_session_context,
    _resolve_configured_workspace_roots,
    _resolve_studio_principal,
    _validate_out_path,
    _validate_session,
)
from opendv_contracts.errors import OpenDVError
from opendv_core.models.graph_a import GraphAOntology
from opendv_service.export.rdf_exporter import export_ontology
from opendv_service.studio.atomic_commit import AtomicCommitService
from opendv_service.studio.bundle import (
    AdminRoleRequiredError,
    ArchiveSecurityViolationError,
    ConfidentialSourceExportUnacknowledgedError,
    ExportRationaleRequiredError,
    compute_effective_session_taint,
    export_bundle,
    import_bundle,
)
from opendv_service.studio.session_export_workflows import resolve_target_promotion_contract_path
from opendv_service.studio.session_manager import SessionAlreadyExistsError
from opendv_service.studio.workspace_security import (
    WorkspaceSecurityManager,
    validate_workspace_containment,
)

if TYPE_CHECKING:
    from opendv_cli.executor import LocalExecutor


_resolve_workspace_target_contract = resolve_target_promotion_contract_path


def handle_export(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """18. `opendv ontology studio export -s <session>` (never reads global `args.format`!)."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    _, meta, sdir, sid = ctx  # type: ignore[misc]
    raw_ver = getattr(args, "version", None)
    if raw_ver:
        if not _VERSION_PATTERN.match(str(raw_ver)):
            return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Invalid version identifier '{raw_ver}'.")
        ver_tag = str(raw_ver) if str(raw_ver).startswith("v") else f"v{raw_ver}"
        vdir = (sdir / "versions" / ver_tag).resolve()
    else:
        vdir = (sdir / "versions" / f"v{meta.head_version.lstrip('v')}").resolve()
    if not vdir.is_relative_to(sdir.resolve()):
        return _err(
            2, "INVALID_WORKSPACE_PATH", f"Version path escapes session directory: '{raw_ver}'"
        )
    ont_file = vdir / "ontology.json"
    if not ont_file.is_file():
        ont_file = vdir / "ontology.yaml"
    if not ont_file.is_file():
        return _err(
            3, "VERSION_NOT_FOUND", f"Ontology artifact not found for version '{vdir.name}'"
        )
    try:
        ont = (
            GraphAOntology.model_validate_json(ont_file.read_text(encoding="utf-8"))
            if ont_file.suffix == ".json"
            else GraphAOntology.model_validate(yaml.safe_load(ont_file.read_text(encoding="utf-8")))
        )
    except (ValidationError, yaml.YAMLError, OSError, ValueError) as exc:
        return _err(1, "CORRUPT_SESSION_STATE", f"Failed to load ontology artifact: {exc}")
    fmt = str(getattr(args, "export_format", None) or "yaml").lower()
    if fmt not in (
        "yaml",
        "yml",
        "json",
        "ttl",
        "turtle",
        "rdf",
        "owl",
        "jsonld",
        "json-ld",
        "nt",
        "ntriples",
        "xml",
    ):
        return _err(
            2,
            "SYNTAX_INVALID_ARGUMENT",
            f"Unsupported export format '{fmt}'. Supported: yaml, json, turtle, jsonld.",
        )
    out_file = getattr(args, "out", None)
    resolved_out_p: Path | None = None
    if out_file is not None:
        out_res = _validate_out_path(str(out_file), executor)
        if isinstance(out_res, tuple):
            return out_res
        resolved_out_p = out_res
    try:
        if fmt in ("ttl", "turtle", "rdf", "owl", "jsonld", "json-ld", "nt", "ntriples", "xml"):
            res = export_ontology(ont, export_format=fmt)
            exported_text = res[0] if isinstance(res, tuple) else str(res)
        elif fmt == "json":
            exported_text = json.dumps(ont.model_dump(mode="json", exclude_none=True), indent=2)
        else:
            exported_text = yaml.safe_dump(ont.model_dump(mode="json", exclude_none=True))
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        return _err(1, "EXECUTION_FAILURE", f"Ontology serialization failed: {exc}")
    if resolved_out_p is not None:
        try:
            _atomic_write_text(resolved_out_p, exported_text)
        except OSError as exc:
            return _err(1, "EXECUTION_FAILURE", f"Failed to write export output: {exc}")
    res_payload: dict[str, Any] = {
        "session_id": sid,
        "version": vdir.name,
        "format": fmt,
        "export_format": fmt,
        "sha256": hashlib.sha256(exported_text.encode("utf-8")).hexdigest(),
        "content": exported_text,
    }
    if resolved_out_p is not None:
        res_payload["out_path"] = str(resolved_out_p)
    return 0, res_payload


def handle_promote(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """19. `opendv ontology studio promote` via `AtomicCommitService.promote_to_workspace`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier != "admin":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Promoting session ontology to workspace requires the 'admin' role.",
        )
    rationale = getattr(args, "rationale", None)
    if not rationale or len(rationale.strip()) < MIN_PROMOTION_RATIONALE_LENGTH:
        return _err(
            1,
            "PROMOTION_RATIONALE_REQUIRED",
            f"Promotion rationale of at least {MIN_PROMOTION_RATIONALE_LENGTH} "
            "characters is required.",
        )
    ack_hist = bool(getattr(args, "acknowledge_historical_contract_warning", False))
    hist_rationale = getattr(args, "historical_contract_rationale", None)
    if ack_hist and (
        not hist_rationale or len(str(hist_rationale).strip()) < MIN_PROMOTION_RATIONALE_LENGTH
    ):
        return _err(
            1,
            "HISTORICAL_CONTRACT_RATIONALE_REQUIRED",
            f"historical_contract_rationale of at least {MIN_PROMOTION_RATIONALE_LENGTH} "
            "characters is required when acknowledge_historical_contract_warning=True.",
        )
    ws_sha, head_sha = getattr(args, "workspace_sha256", None), getattr(args, "head_sha256", None)
    if not ws_sha or not head_sha:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing --workspace-sha256 or --head-sha256")
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, meta, _, sid = ctx  # type: ignore[misc]
    base_ws = (executor.workspace_root if executor else Path.cwd()).resolve()
    allowed_roots = _resolve_configured_workspace_roots(base_ws) or [base_ws]
    ws_arg = getattr(args, "workspace", None)
    if ws_arg:
        raw_ws_path = Path(ws_arg)
        try:
            target_ws = validate_workspace_containment(
                raw_ws_path if raw_ws_path.is_absolute() else (base_ws / raw_ws_path),
                allowed_roots=allowed_roots,
                is_remote_request=False,
            )
        except OpenDVError as exc:
            return _err(2, "INVALID_WORKSPACE_PATH", str(exc))
    else:
        target_ws = base_ws
    if target_ws.suffix.lower() not in (".yaml", ".yml", ".json"):
        target_ws.mkdir(parents=True, exist_ok=True)
    target_contract = _resolve_workspace_target_contract(target_ws, meta.domain_namespace)
    clean_ws_sha = str(ws_sha).strip().strip('"').lower()
    if not target_contract.is_file() and clean_ws_sha != "0" * 64:
        return _err(
            5,
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"Greenfield workspace promotion requires --workspace-sha256={'0' * 64}, "
            f"got '{ws_sha}'.",
        )
    commit_svc = AtomicCommitService(
        session_manager=session_mgr, security_manager=session_mgr.security_manager
    )
    try:
        res = commit_svc.promote_to_workspace(
            session_id=sid,
            target_workspace=target_contract,
            expected_workspace_sha256=clean_ws_sha,
            expected_head_sha256=str(head_sha).strip().strip('"'),
            rationale=rationale.strip(),
            actor=principal,
            actor_role=role_tier,
            acknowledge_historical_contract_warning=ack_hist,
            historical_contract_rationale=hist_rationale,
            allowed_workspace_roots=allowed_roots,
            is_remote_request=False,
            enforce_lineage=(not ack_hist),
            allow_noop=False,
        )
        if isinstance(res, dict):
            return 0, res
        return 0, {
            "session_id": sid,
            "promoted_version": res.promoted_version.lstrip("v"),
            "target_contract_path": str(res.target_contract_path),
            "head_sha256": res.head_sha256,
            "workspace_sha256": res.workspace_sha256,
            "promoted_at": res.promoted_at,
        }
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)


def handle_bundle_export(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """20. `opendv ontology studio bundle export`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "automated":
        return _err(
            7,
            "AGENT_OPERATION_FORBIDDEN",
            "Automated agents are not permitted to export Studio session bundles.",
        )
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Read-only viewer role is not permitted to export Studio session bundles.",
        )
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, _, sdir, sid = ctx  # type: ignore[misc]
    out_res = _validate_out_path(
        str(getattr(args, "out", None) or f"{sid}.odvstudio.zip"), executor
    )
    if isinstance(out_res, tuple):
        return out_res
    try:
        res_path = export_bundle(
            session_dir=sdir,
            output_zip_path=out_res,
            security_manager=session_mgr.security_manager,
            acknowledge_confidential=bool(
                getattr(args, "acknowledge_confidential_source_export", False)
            ),
            export_rationale=getattr(args, "export_rationale", None),
            admin=(role_tier == "admin"),
            actor=principal,
            overwrite=True,
            allow_cli_output_path=(executor is None or getattr(executor, "is_local", True)),
        )
    except AdminRoleRequiredError as exc:
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", str(exc))
    except ConfidentialSourceExportUnacknowledgedError as exc:
        return _err(4, "CONFIDENTIAL_SOURCE_EXPORT_UNACKNOWLEDGED", str(exc))
    except ExportRationaleRequiredError as exc:
        return _err(1, "EXPORT_RATIONALE_REQUIRED", str(exc))
    except (ArchiveSecurityViolationError, OpenDVError) as exc:
        return _err_from_opendv_error(exc)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        return _err(1, "EXECUTION_FAILURE", str(exc))
    return 0, {
        "session_id": sid,
        "bundle_path": str(res_path),
        "size_bytes": res_path.stat().st_size,
    }


def handle_bundle_import(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """21. `opendv ontology studio bundle import -f <in_zip>`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "automated":
        return _err(
            7,
            "AGENT_OPERATION_FORBIDDEN",
            "Automated agents are not permitted to import Studio bundles.",
        )
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            f"Role '{role_tier}' is not authorized to import Studio bundles.",
        )
    ack_unverified, force = (
        bool(getattr(args, "acknowledge_unverified_import_attribution", False)),
        bool(getattr(args, "force", False)),
    )
    if (ack_unverified or force) and role_tier != "admin":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "--acknowledge-unverified-import-attribution and --force require the 'admin' role.",
        )
    in_zip = getattr(args, "file", None)
    if not in_zip:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --file flag")
    in_p = Path(in_zip)
    if not in_p.is_absolute() and executor:
        in_p = (executor.workspace_root / in_p).resolve()
    if not in_p.is_file():
        return _err(3, "FILE_NOT_FOUND", f"File not found: {in_zip}")
    studio_root = _get_studio_root(executor)
    sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
    new_sid = getattr(args, "new_session_id", None)
    if new_sid:
        err_new = _validate_session(new_sid)
        if err_new:
            return err_new
    try:
        res = import_bundle(
            zip_path=in_p,
            target_dir=studio_root,
            security_manager=sec_mgr,
            new_session_id=new_sid,
            acknowledge_unverified_import_attribution=ack_unverified,
            acknowledgement_rationale=getattr(args, "acknowledgement_rationale", None),
            imported_by=principal,
            force=force,
            actor_role=role_tier,
        )
    except (SessionAlreadyExistsError, FileExistsError) as exc:
        return _err(6, "SESSION_ALREADY_EXISTS", str(exc))
    except (ArchiveSecurityViolationError, OpenDVError) as exc:
        return _err_from_opendv_error(exc)
    except zipfile.BadZipFile as exc:
        return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"Corrupt bundle zip archive: {exc}")
    except (ValueError, KeyError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        return _err(4, "IMPORT_GROUNDING_VIOLATION", str(exc))
    return 0, {
        "session_id": res.session_id,
        "status": "IMPORTED",
        "version": res.head_version,
        "head_sha256": res.head_sha256,
    }


def handle_bundle_clone(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """22. `opendv ontology studio bundle clone`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "automated":
        return _err(
            7,
            "AGENT_OPERATION_FORBIDDEN",
            "Automated agents are not permitted to clone Studio sessions.",
        )
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Read-only viewer role is not permitted to clone Studio sessions.",
        )
    sid = getattr(args, "session", None)
    err = _validate_session(sid)
    if err:
        return err
    new_sid = getattr(args, "new_session_id", None) or f"s-{secrets.token_hex(8)}"
    err_new = _validate_session(new_sid)
    if err_new:
        return err_new
    session_mgr = _get_session_manager(executor)
    res_sess = _require_session(session_mgr, str(sid))
    if isinstance(res_sess, tuple):
        return res_sess
    sdir = session_mgr.get_session_dir(str(sid))
    if bool(compute_effective_session_taint(sdir)) and role_tier != "admin":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Cloning a session containing confidential/tainted sources requires the 'admin' role.",
        )
    try:
        res = session_mgr.clone_session(str(sid), new_sid, actor=principal, actor_role=role_tier)
    except (FileExistsError, SessionAlreadyExistsError):
        return _err(6, "SESSION_ALREADY_EXISTS", f"Session '{new_sid}' already exists")
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)
    except (OSError, RuntimeError, ValueError) as exc:
        return _err(1, "INTERNAL_CLONE_ERROR", f"Failed to clone session: {exc}")
    return 0, {
        "source_session_id": sid,
        "new_session_id": res.session_id,
        "session_id": res.session_id,
        "status": "CLONED",
        "head_version": res.head_version,
        "head_sha256": res.head_sha256,
    }
