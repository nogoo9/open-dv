"""Startup crash recovery and reconciliation protocol under L5 session lock."""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from opendv_core.genesis import GENESIS_VERSION
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.studio.atomic_commit_audit import write_promotion_record
from opendv_service.studio.atomic_commit_errors import DEFAULT_STAGING_GRACE_SECONDS
from opendv_service.studio.atomic_commit_grounding import (
    fsync_directory_best_effort,
    parse_semver_tuple,
)
from opendv_service.studio.locks import DEFAULT_LOCK_TIMEOUT_SECONDS, reentrant_session_lock
from opendv_service.studio.session_manager import SessionMetadata, normalize_version_tag
from opendv_service.studio.workspace_security import (
    InvalidWorkspacePathError,
    RemoteServerPathForbiddenError,
    replace_with_retry,
    validate_workspace_containment,
)

logger = logging.getLogger(__name__)


def execute_reconcile_and_recover_session(
    service: Any,
    session_id: str,
    *,
    staging_grace_seconds: float = DEFAULT_STAGING_GRACE_SECONDS,
    timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> None:
    """Execute deterministic crash recovery and reconciliation under L5 session lock."""
    session_dir = service.session_manager.get_session_dir(session_id)
    session_json_path = session_dir / "session.json"
    if not session_dir.exists() or not session_json_path.exists():
        return

    lock_path = session_dir / ".session.lock"
    with reentrant_session_lock(lock_path, session_id=session_id, timeout=timeout_sec):
        if not session_json_path.exists():
            return

        session = SessionMetadata.model_validate_json(
            session_json_path.read_text(encoding="utf-8")
        )
        audit_file = session_dir / "audit.jsonl"

        # 1. Audit Chain Integrity Verification & WAL Roll-Forward
        tail_sha256 = service._verify_audit_chain_or_raise(session_id, session, mutate=True)
        if tail_sha256 != session.audit_chain_head_sha256:
            session.audit_chain_head_sha256 = tail_sha256
            service._write_session_json(session_dir, session)

        # 2. Missing Active Version Directory Recovery
        if session.head_version != GENESIS_VERSION:
            active_version_tag = normalize_version_tag(session.head_version)
            active_version_dir = session_dir / "versions" / active_version_tag
            if not active_version_dir.exists():
                repaired = False
                for staging_dir in session_dir.glob(".staging-*"):
                    if staging_dir.is_dir():
                        meta_file = staging_dir / "meta.json"
                        ont_file = staging_dir / "ontology.json"
                        if meta_file.exists() and ont_file.exists():
                            try:
                                mdata = json.loads(meta_file.read_text(encoding="utf-8"))
                                staged_ont = GraphAOntology.model_validate_json(
                                    ont_file.read_text(encoding="utf-8")
                                )
                                if (
                                    mdata.get("target_sha256") == session.head_sha256
                                    and compute_ontology_sha256(staged_ont)
                                    == session.head_sha256
                                ):
                                    active_version_dir.parent.mkdir(parents=True, exist_ok=True)
                                    replace_with_retry(staging_dir, active_version_dir)
                                    fsync_directory_best_effort(active_version_dir.parent)
                                    repaired = True
                                    break
                            except (OSError, json.JSONDecodeError, ValidationError):
                                continue
                if not repaired:
                    logger.warning(
                        "Active version directory '%s' missing and could not be recovered",
                        active_version_tag,
                    )

        # 3. Staging Cleanup Safety Invariant (respecting staging_grace_seconds)
        committed_set = set(session.committed_versions)
        now_epoch = time.time()
        for staging_dir in list(session_dir.glob(".staging-*")):
            if staging_dir.is_dir():
                meta_file = staging_dir / "meta.json"
                keep = False
                if meta_file.exists():
                    try:
                        mdata = json.loads(meta_file.read_text(encoding="utf-8"))
                        t_sha = mdata.get("target_sha256")
                        t_ver = mdata.get("version") or mdata.get("target_version")
                        if (
                            t_sha == session.head_sha256
                            or (t_ver and t_ver in committed_set)
                            or (t_ver and t_ver.lstrip("v") in committed_set)
                            or (t_ver and normalize_version_tag(t_ver) in committed_set)
                        ):
                            keep = True
                    except (OSError, json.JSONDecodeError):
                        pass

                if not keep and staging_grace_seconds > 0:
                    try:
                        age = now_epoch - staging_dir.stat().st_mtime
                        if age < staging_grace_seconds:
                            keep = True
                    except OSError:
                        pass

                if not keep:
                    shutil.rmtree(staging_dir, ignore_errors=True)

        # 4. Orphan Version Directory Reconciliation (SemVer-sorted & K_audit-gated)
        active_pending_intents = service._collect_active_pending_commit_intents(
            audit_file, session
        )
        verified_committed_records = service._collect_verified_committed_records(
            audit_file, session
        )
        versions_root = session_dir / "versions"
        if versions_root.exists():
            semver_version_dirs: list[tuple[tuple[int, int, int], str, str, Path]] = []
            for v_dir in versions_root.iterdir():
                if not v_dir.is_dir():
                    continue
                v_name = v_dir.name
                v_clean = v_name.lstrip("v")
                if v_clean == GENESIS_VERSION:
                    continue
                try:
                    v_tuple = parse_semver_tuple(v_clean)
                except ValueError:
                    continue
                semver_version_dirs.append((v_tuple, v_name, v_clean, v_dir))

            semver_version_dirs.sort(key=lambda item: (item[0], item[1]))

            for v_tuple, v_name, v_clean, v_dir in semver_version_dirs:
                is_committed = (
                    v_name in committed_set
                    or v_clean in committed_set
                    or normalize_version_tag(v_clean) in committed_set
                )
                if is_committed:
                    continue

                if v_tuple > parse_semver_tuple(session.head_version):
                    meta_path = (
                        v_dir / "meta.json"
                        if (v_dir / "meta.json").exists()
                        else v_dir / "metadata.json"
                    )
                    ont_path = v_dir / "ontology.json"
                    reconciled = False
                    norm_tag = normalize_version_tag(v_clean)

                    if meta_path.exists() and ont_path.exists():
                        try:
                            mdata = json.loads(meta_path.read_text(encoding="utf-8"))
                            ont_model = GraphAOntology.model_validate_json(
                                ont_path.read_text(encoding="utf-8")
                            )
                            computed_sha = compute_ontology_sha256(ont_model)
                            parent_sha = str(mdata.get("parent_sha256") or "").strip().lower()
                            target_sha = str(mdata.get("target_sha256") or "").strip().lower()
                            intent_key = (norm_tag, target_sha, parent_sha)

                            if (
                                parent_sha == session.head_sha256
                                and computed_sha == target_sha
                                and intent_key in verified_committed_records
                            ):
                                now_iso = datetime.now(UTC).isoformat()
                                session.head_version = v_clean
                                session.head_sha256 = target_sha
                                if norm_tag not in session.committed_versions:
                                    session.committed_versions.append(norm_tag)
                                session.updated_at = now_iso
                                committed_set.add(norm_tag)

                                service._mark_proposals_post_commit(
                                    session_dir=session_dir,
                                    accepted_proposal_id=mdata.get("proposal_id"),
                                    target_sha256=target_sha,
                                    now_iso=now_iso,
                                )
                                service._write_session_json(session_dir, session)
                                reconciled = True
                            elif (
                                parent_sha == session.head_sha256
                                and computed_sha == target_sha
                                and intent_key in active_pending_intents
                            ):
                                now_iso = datetime.now(UTC).isoformat()
                                session.head_version = v_clean
                                session.head_sha256 = target_sha
                                if norm_tag not in session.committed_versions:
                                    session.committed_versions.append(norm_tag)
                                session.updated_at = now_iso
                                committed_set.add(norm_tag)
                                active_pending_intents.pop(intent_key, None)

                                service._mark_proposals_post_commit(
                                    session_dir=session_dir,
                                    accepted_proposal_id=mdata.get("proposal_id"),
                                    target_sha256=target_sha,
                                    now_iso=now_iso,
                                )

                                rec_entry = service._append_raw_audit_entry_under_lock(
                                    session_dir=session_dir,
                                    session_id=session_id,
                                    action="COMMITTED",
                                    target_version=norm_tag,
                                    head_sha256=target_sha,
                                    actor="system:recovery",
                                    details={
                                        "target_version": norm_tag,
                                        "target_sha256": target_sha,
                                        "parent_sha256": parent_sha,
                                        "reconciled": True,
                                    },
                                    prev_audit_sha=session.audit_chain_head_sha256,
                                )
                                session.audit_chain_head_sha256 = rec_entry["audit_sha256"]
                                service._write_session_json(session_dir, session)
                                reconciled = True
                        except (OSError, json.JSONDecodeError, ValidationError) as exc:
                            logger.warning(
                                "Failed to validate forward version %s for recovery: %s",
                                v_name,
                                exc,
                            )

                    if not reconciled:
                        quarantine_dir = (
                            session_dir
                            / ".quarantine"
                            / f"{v_name}-{int(time.time())}-{secrets.token_hex(4)}"
                        )
                        quarantine_dir.parent.mkdir(parents=True, exist_ok=True)
                        replace_with_retry(v_dir, quarantine_dir)

        # 5 & 6. Incomplete PENDING_COMMIT / PROMOTION_INTENT reconciliation from audit tail
        if audit_file.exists():
            records = service._read_valid_audit_records(audit_file)
            if records:
                last_rec = records[-1]
                last_action = last_rec.get("action")

                if last_action == "PENDING_COMMIT":
                    p_details = last_rec.get("details", {})
                    p_version = str(p_details.get("target_version") or "")
                    p_target_sha = p_details.get("target_sha256")
                    p_parent_sha = p_details.get("parent_sha256")
                    p_proposal_id = p_details.get("proposal_id")

                    clean_v = p_version.lstrip("v")
                    norm_tag = normalize_version_tag(clean_v) if clean_v else ""
                    v_dir = session_dir / "versions" / norm_tag if norm_tag else None

                    can_finalize = False
                    if (
                        v_dir is not None
                        and v_dir.exists()
                        and (v_dir / "ontology.json").exists()
                        and clean_v
                    ):
                        try:
                            v_tuple = parse_semver_tuple(clean_v)
                            head_tuple = parse_semver_tuple(session.head_version)
                            forward_ok = (
                                p_parent_sha == session.head_sha256 and v_tuple > head_tuple
                            ) or (
                                p_target_sha == session.head_sha256
                                and clean_v == session.head_version
                            )
                            if forward_ok:
                                ont_model = GraphAOntology.model_validate_json(
                                    (v_dir / "ontology.json").read_text(encoding="utf-8")
                                )
                                if compute_ontology_sha256(ont_model) == p_target_sha:
                                    can_finalize = True
                        except (OSError, json.JSONDecodeError, ValidationError, ValueError):
                            can_finalize = False

                    if can_finalize and norm_tag and isinstance(p_target_sha, str):
                        now_iso = datetime.now(UTC).isoformat()
                        session.head_version = clean_v
                        session.head_sha256 = p_target_sha
                        if norm_tag not in session.committed_versions:
                            session.committed_versions.append(norm_tag)
                        session.updated_at = now_iso

                        service._mark_proposals_post_commit(
                            session_dir=session_dir,
                            accepted_proposal_id=p_proposal_id,
                            target_sha256=p_target_sha,
                            now_iso=now_iso,
                        )

                        committed_entry = service._append_raw_audit_entry_under_lock(
                            session_dir=session_dir,
                            session_id=session_id,
                            action="COMMITTED",
                            target_version=norm_tag,
                            head_sha256=p_target_sha,
                            actor="system:recovery",
                            details=p_details,
                            prev_audit_sha=last_rec["audit_sha256"],
                        )
                        session.audit_chain_head_sha256 = committed_entry["audit_sha256"]
                        service._write_session_json(session_dir, session)
                    else:
                        abort_entry = service._append_raw_audit_entry_under_lock(
                            session_dir=session_dir,
                            session_id=session_id,
                            action="COMMIT_ABORTED",
                            target_version=str(p_version or "unknown"),
                            head_sha256=session.head_sha256,
                            actor="system:recovery",
                            details={"aborted_pending_sha256": p_target_sha},
                            prev_audit_sha=last_rec["audit_sha256"],
                        )
                        session.audit_chain_head_sha256 = abort_entry["audit_sha256"]
                        service._write_session_json(session_dir, session)

                elif last_action == "PROMOTION_INTENT":
                    p_details = last_rec.get("details", {})
                    ws_path_str = p_details.get("workspace_path")
                    target_head_sha = p_details.get("target_head_sha256")
                    target_ver = str(p_details.get("target_version") or session.head_version)
                    norm_ver = normalize_version_tag(target_ver)

                    promoted_ok = False
                    resolved_ws_file: Path | None = None
                    if isinstance(ws_path_str, str) and isinstance(target_head_sha, str):
                        raw_ws_file = Path(ws_path_str)
                        if not raw_ws_file.is_symlink():
                            try:
                                env_roots = os.environ.get("OPENDV_WORKSPACE_ROOTS", "").strip()
                                sroot = service.security_manager.studio_root
                                default_ws_roots: list[Path | str] = [
                                    os.getcwd(),
                                    sroot.parent,
                                ]
                                if sroot.parent.name == ".opendv":
                                    default_ws_roots.append(sroot.parent.parent)
                                recovery_roots: Sequence[Path | str] | None = (
                                    None if env_roots else default_ws_roots
                                )
                                resolved_ws_file = validate_workspace_containment(
                                    raw_ws_file,
                                    allowed_roots=recovery_roots,
                                )
                            except (
                                InvalidWorkspacePathError,
                                RemoteServerPathForbiddenError,
                                OSError,
                            ):
                                resolved_ws_file = None

                        if (
                            resolved_ws_file is not None
                            and not resolved_ws_file.is_symlink()
                            and resolved_ws_file.is_file()
                        ):
                            try:
                                raw_ws = resolved_ws_file.read_bytes()
                                if resolved_ws_file.suffix.lower() == ".json":
                                    ws_ont = GraphAOntology.model_validate_json(
                                        raw_ws.decode("utf-8")
                                    )
                                else:
                                    ws_ont = GraphAOntology.model_validate(
                                        yaml.safe_load(raw_ws.decode("utf-8"))
                                    )
                                if compute_ontology_sha256(ws_ont) == target_head_sha:
                                    promoted_ok = True
                            except (OSError, ValidationError, ValueError, yaml.YAMLError):
                                promoted_ok = False

                    now_iso = datetime.now(UTC).isoformat()
                    if promoted_ok and isinstance(target_head_sha, str):
                        promo_record = {
                            "session_id": session_id,
                            "promoted_version": norm_ver,
                            "head_sha256": target_head_sha,
                            "workspace_sha256": target_head_sha,
                            "pre_workspace_sha256": str(
                                p_details.get("pre_workspace_sha256") or "0" * 64
                            ),
                            "workspace_path": str(resolved_ws_file or ws_path_str),
                            "rationale": str(p_details.get("rationale") or ""),
                            "promoted_by": str(last_rec.get("actor") or "system:recovery"),
                            "promoted_at": now_iso,
                            "reconciled": True,
                        }
                        write_promotion_record(session_dir, promo_record)

                        if norm_ver not in session.promoted_versions:
                            session.promoted_versions.append(norm_ver)
                        session.last_promoted_sha256 = target_head_sha
                        session.updated_at = now_iso
                        rec_promo = service._append_raw_audit_entry_under_lock(
                            session_dir=session_dir,
                            session_id=session_id,
                            action="PROMOTION_COMMITTED",
                            target_version=norm_ver,
                            head_sha256=session.head_sha256,
                            actor="system:recovery",
                            details={**p_details, "reconciled": True},
                            prev_audit_sha=last_rec["audit_sha256"],
                        )
                        session.audit_chain_head_sha256 = rec_promo["audit_sha256"]
                        service._write_session_json(session_dir, session)
                    else:
                        abort_promo = service._append_raw_audit_entry_under_lock(
                            session_dir=session_dir,
                            session_id=session_id,
                            action="PROMOTION_ABORTED",
                            target_version=norm_ver,
                            head_sha256=session.head_sha256,
                            actor="system:recovery",
                            details={**p_details, "reconciled": True},
                            prev_audit_sha=last_rec["audit_sha256"],
                        )
                        session.audit_chain_head_sha256 = abort_promo["audit_sha256"]
                        service._write_session_json(session_dir, session)

                elif last_action == "PROMOTION_COMMITTED":
                    p_details = (
                        last_rec.get("details")
                        if isinstance(last_rec.get("details"), dict)
                        else {}
                    )
                    raw_p_ver = str(
                        p_details.get("target_version") or last_rec.get("target_version") or ""
                    ).strip()
                    norm_p_ver = normalize_version_tag(raw_p_ver) if raw_p_ver else ""
                    ws_sha = (
                        str(
                            p_details.get("workspace_sha256")
                            or p_details.get("target_head_sha256")
                            or last_rec.get("head_sha256")
                            or ""
                        )
                        .strip()
                        .lower()
                    )
                    promo_changed = False
                    if norm_p_ver and norm_p_ver not in session.promoted_versions:
                        session.promoted_versions.append(norm_p_ver)
                        promo_changed = True
                    if len(ws_sha) == 64 and session.last_promoted_sha256 != ws_sha:
                        session.last_promoted_sha256 = ws_sha
                        promo_changed = True
                    if promo_changed:
                        session.updated_at = datetime.now(UTC).isoformat()
                        service._write_session_json(session_dir, session)
