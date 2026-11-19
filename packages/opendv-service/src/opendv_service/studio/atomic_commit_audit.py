"""Session version loading, proposal updates, and audit ledger I/O helpers."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from opendv_contracts.dtos.ontology import StewardWaiverRecord
from opendv_core.genesis import GENESIS_VERSION, create_genesis_ontology
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import dumps_jcs_bytes
from opendv_service.studio.atomic_commit_errors import atomic_write_bytes
from opendv_service.studio.session_manager import (
    SessionMetadata,
    StudioSessionManager,
    TamperEvidentChainCorruptError,
    normalize_version_tag,
    reconcile_or_verify_audit_ledger,
    sanitize_domain_namespace,
)
from opendv_service.studio.workspace_security import (
    WorkspaceSecurityManager,
    set_owner_only_permissions,
)

logger = logging.getLogger(__name__)


def load_version_ontology(
    session_dir: Path,
    session: SessionMetadata,
    version_str: str,
) -> GraphAOntology:
    """Load ontology artifact for `version_str`, falling back to GenesisDraft on `0.0.0`."""
    clean_ver = version_str.lstrip("v")
    vdir = session_dir / "versions" / f"v{clean_ver}"
    ont_json = vdir / "ontology.json"
    ont_yaml = vdir / "ontology.yaml"

    if ont_json.is_file():
        return GraphAOntology.model_validate_json(ont_json.read_text(encoding="utf-8"))
    if ont_yaml.is_file():
        ont_dict = yaml.safe_load(ont_yaml.read_text(encoding="utf-8"))
        return GraphAOntology.model_validate(ont_dict)
    if clean_ver == GENESIS_VERSION:
        clean_ns = sanitize_domain_namespace(session.domain_namespace)
        return create_genesis_ontology(
            domain_namespace=clean_ns,
            enterprise_owner=session.enterprise_owner or "Enterprise Architecture",
        )
    raise FileNotFoundError(f"Base ontology artifact missing for version 'v{clean_ver}'")


def mark_proposals_post_commit(
    session_dir: Path,
    accepted_proposal_id: str | None,
    target_sha256: str,
    now_iso: str,
    evaluation: dict[str, Any] | None = None,
    *,
    accepted_by: str | None = None,
    waivers: Sequence[StewardWaiverRecord] | None = None,
) -> None:
    """Atomically mark accepted proposal ACCEPTED and stale other active proposals."""
    proposals_dir = session_dir / "proposals"
    if not proposals_dir.is_dir():
        return

    serialized_waivers: list[dict[str, Any]] | None = None
    if waivers:
        serialized_waivers = [w.model_dump(mode="json", exclude_none=True) for w in waivers]
    elif evaluation and isinstance(evaluation.get("waivers"), list):
        serialized_waivers = list(evaluation["waivers"])
    elif evaluation and isinstance(evaluation.get("waiver"), dict):
        serialized_waivers = [dict(evaluation["waiver"])]

    for pfile in sorted(proposals_dir.glob("*.json")):
        try:
            pdata = json.loads(pfile.read_text(encoding="utf-8"))
            if not isinstance(pdata, dict):
                continue
            pid = str(pdata.get("proposal_id") or pfile.stem)
            changed = False

            if accepted_proposal_id and (
                pid == accepted_proposal_id or pfile.stem == accepted_proposal_id
            ):
                pdata["status"] = "ACCEPTED"
                pdata["updated_at"] = now_iso
                pdata["resolved_at"] = now_iso
                if accepted_by:
                    pdata["accepted_by"] = accepted_by
                if evaluation and "waiver" in evaluation:
                    pdata["waiver"] = evaluation["waiver"]
                if serialized_waivers is not None:
                    pdata["waivers"] = serialized_waivers
                changed = True
            elif pdata.get("status") in (None, "PENDING", "PROPOSED"):
                p_base = (
                    pdata.get("base_sha256")
                    or pdata.get("base_ontology_sha256")
                    or (
                        pdata.get("delta", {}).get("parent_sha256")
                        if isinstance(pdata.get("delta"), dict)
                        else None
                    )
                )
                if p_base != target_sha256:
                    pdata["status"] = "STALE"
                    pdata["updated_at"] = now_iso
                    changed = True

            if changed:
                payload = json.dumps(pdata, indent=2).encode("utf-8")
                atomic_write_bytes(
                    pfile,
                    payload,
                    tmp_name=f".{pfile.stem}.{secrets.token_hex(4)}.tmp",
                )
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to update proposal file %s: %s", pfile.name, exc)


def collect_verified_committed_records(
    audit_file: Path,
    session: SessionMetadata,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Collect locally K_audit-verified COMMITTED records for post-crash roll-forward."""
    if not audit_file.is_file():
        return {}
    records = read_valid_audit_records(audit_file)
    anchor_sha = session.imported_audit_anchor_sha256
    seen_anchor = anchor_sha is None
    committed_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    last_pending_parent_by_ver_sha: dict[tuple[str, str], str] = {}

    for rec in records:
        if not seen_anchor and rec.get("audit_sha256") == anchor_sha:
            seen_anchor = True
        if not seen_anchor:
            continue

        action = str(rec.get("action") or "")
        det = rec.get("details") if isinstance(rec.get("details"), dict) else {}
        raw_tv = str(det.get("target_version") or rec.get("target_version") or "").strip()
        norm_tv = (
            normalize_version_tag(raw_tv) if raw_tv and raw_tv.lower() != "unknown" else ""
        )
        t_sha = str(det.get("target_sha256") or rec.get("head_sha256") or "").strip().lower()
        p_sha = str(det.get("parent_sha256") or "").strip().lower()

        if action == "PENDING_COMMIT" and norm_tv and len(t_sha) == 64 and len(p_sha) == 64:
            last_pending_parent_by_ver_sha[(norm_tv, t_sha)] = p_sha
        elif action == "COMMITTED" and norm_tv and len(t_sha) == 64:
            resolved_p_sha = p_sha or last_pending_parent_by_ver_sha.get((norm_tv, t_sha), "")
            if len(resolved_p_sha) == 64:
                committed_map[(norm_tv, t_sha, resolved_p_sha)] = rec

    return committed_map


def collect_active_pending_commit_intents(
    audit_file: Path,
    session: SessionMetadata,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Collect locally K_audit-verified PENDING_COMMIT intents without subsequent closure."""
    if not audit_file.is_file():
        return {}
    records = read_valid_audit_records(audit_file)
    anchor_sha = session.imported_audit_anchor_sha256
    seen_anchor = anchor_sha is None
    active: dict[tuple[str, str, str], dict[str, Any]] = {}

    for rec in records:
        if not seen_anchor and rec.get("audit_sha256") == anchor_sha:
            seen_anchor = True
        if not seen_anchor:
            continue

        action = str(rec.get("action") or "")
        det = rec.get("details") if isinstance(rec.get("details"), dict) else {}

        if action == "PENDING_COMMIT":
            raw_tv = str(det.get("target_version") or rec.get("target_version") or "").strip()
            norm_tv = normalize_version_tag(raw_tv) if raw_tv else ""
            t_sha = (
                str(det.get("target_sha256") or rec.get("head_sha256") or "").strip().lower()
            )
            p_sha = str(det.get("parent_sha256") or "").strip().lower()
            if norm_tv and len(t_sha) == 64 and len(p_sha) == 64:
                active[(norm_tv, t_sha, p_sha)] = rec
        elif action == "COMMITTED":
            raw_tv = str(det.get("target_version") or rec.get("target_version") or "").strip()
            norm_tv = normalize_version_tag(raw_tv) if raw_tv else ""
            t_sha = (
                str(det.get("target_sha256") or rec.get("head_sha256") or "").strip().lower()
            )
            p_sha = str(det.get("parent_sha256") or "").strip().lower()
            to_remove = [
                k
                for k in active
                if (t_sha and k[1] == t_sha)
                or (
                    norm_tv
                    and k[0] == norm_tv
                    and (not t_sha or k[1] == t_sha)
                    and (not p_sha or k[2] == p_sha)
                )
            ]
            for k in to_remove:
                active.pop(k, None)
        elif action == "COMMIT_ABORTED":
            raw_tv = str(det.get("target_version") or rec.get("target_version") or "").strip()
            norm_tv = (
                normalize_version_tag(raw_tv) if raw_tv and raw_tv.lower() != "unknown" else ""
            )
            aborted_sha = (
                str(det.get("aborted_pending_sha256") or det.get("target_sha256") or "")
                .strip()
                .lower()
            )
            p_sha = str(det.get("parent_sha256") or "").strip().lower()
            to_remove = [
                k
                for k in active
                if (aborted_sha and k[1] == aborted_sha)
                or (norm_tv and k[0] == norm_tv and (not p_sha or k[2] == p_sha))
            ]
            for k in to_remove:
                active.pop(k, None)

    return active


def verify_audit_chain_or_raise(
    session_manager: StudioSessionManager,
    security_manager: WorkspaceSecurityManager,
    session_id: str,
    session: SessionMetadata,
    *,
    mutate: bool = False,
) -> str:
    """Verify the full HMAC and hash chaining of audit.jsonl, raising error if corrupt."""
    session_dir = session_manager.get_session_dir(session_id)
    audit_file = session_dir / "audit.jsonl"

    if not audit_file.exists():
        raise TamperEvidentChainCorruptError(f"Audit ledger missing for session '{session_id}'")

    tail_sha256 = reconcile_or_verify_audit_ledger(
        session_dir,
        session,
        security_manager,
        mutate=mutate,
    )
    if not tail_sha256:
        raise TamperEvidentChainCorruptError(
            f"Audit ledger contains no valid lines for session '{session_id}'"
        )
    return tail_sha256


def read_valid_audit_records(audit_file: Path) -> list[dict[str, Any]]:
    """Read and parse all valid JSON lines from audit.jsonl."""
    records: list[dict[str, Any]] = []
    for line in audit_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            with contextlib.suppress(json.JSONDecodeError):
                records.append(json.loads(line))
    return records


def append_raw_audit_entry_under_lock(
    security_manager: WorkspaceSecurityManager,
    session_dir: Path,
    session_id: str,
    action: str,
    target_version: str,
    head_sha256: str,
    actor: str,
    details: dict[str, Any],
    prev_audit_sha: str | None,
) -> dict[str, Any]:
    """Format, sign with K_audit, and append an audit entry to audit.jsonl under lock."""
    now_iso = datetime.now(UTC).isoformat()
    entry: dict[str, Any] = {
        "timestamp": now_iso,
        "session_id": session_id,
        "actor": actor,
        "action": action,
        "target_version": target_version,
        "head_sha256": head_sha256,
        "details": details,
        "prev_audit_sha256": prev_audit_sha,
    }

    canonical_bytes = dumps_jcs_bytes(entry)
    entry_sha256 = hashlib.sha256(canonical_bytes).hexdigest().lower()
    hmac_sig = security_manager.sign_audit_entry(canonical_bytes)

    entry["audit_sha256"] = entry_sha256
    entry["hmac_signature"] = hmac_sig

    audit_file = session_dir / "audit.jsonl"
    with audit_file.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(entry) + "\n")
        fp.flush()
        os.fsync(fp.fileno())

    set_owner_only_permissions(audit_file)
    return entry


def write_session_json(session_dir: Path, metadata: SessionMetadata) -> None:
    """Atomically overwrite session.json using a temporary file with retry."""
    atomic_write_bytes(
        session_dir / "session.json",
        metadata.model_dump_json(indent=2).encode("utf-8"),
        tmp_name=f".session.json.{secrets.token_hex(4)}.tmp",
        truncate=True,
    )


def write_promotion_record(session_dir: Path, promo_record: dict[str, Any]) -> None:
    """Atomically write promotion.json inside the session directory."""
    atomic_write_bytes(
        session_dir / "promotion.json",
        json.dumps(promo_record, indent=2).encode("utf-8"),
        tmp_name=f".promotion.{secrets.token_hex(4)}.tmp",
    )
