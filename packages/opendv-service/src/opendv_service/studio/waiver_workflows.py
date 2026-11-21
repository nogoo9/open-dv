"""Shared Studio steward waiver verification, HMAC signing, and persistence workflows."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from opendv_contracts.dtos.ontology import OntologyDelta, StewardWaiverRecord
from opendv_contracts.jcs import dumps_jcs_bytes
from opendv_core.models.graph_a import GraphAOntology
from opendv_service.studio.atomic_commit import collect_touched_concept_uris
from opendv_service.studio.delta_workflows import StudioWorkflowError, parse_strong_etag_or_error
from opendv_service.studio.locks import reentrant_session_lock
from opendv_service.studio.session_manager import (
    SessionMetadata,
    StudioSessionManager,
    append_signed_audit_record,
)
from opendv_service.studio.workspace_security import replace_with_retry, set_owner_only_permissions


def normalize_ungrounded_list(raw: list[str] | str | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    return [str(item).strip() for item in raw if str(item).strip()]


def proposal_uuid_from_str(proposal_id: str | None) -> uuid.UUID:
    """Deterministically map a string proposal ID or UUID string to a valid UUIDv4."""
    if proposal_id:
        with contextlib.suppress(ValueError):
            parsed = uuid.UUID(str(proposal_id))
            if parsed.version == 4:
                return parsed
        digest16 = hashlib.sha256(f"opendv:proposal:{proposal_id}".encode()).digest()[:16]
        return uuid.UUID(bytes=digest16, version=4)
    return uuid.uuid4()


def parse_waiver_expires_at(raw_expires_at: Any) -> tuple[bool, datetime | None]:
    """Return `(is_expired, parsed_utc_dt)` for a stored waiver `expires_at` field."""
    if raw_expires_at is None or (isinstance(raw_expires_at, str) and not raw_expires_at.strip()):
        return False, None
    if not isinstance(raw_expires_at, str):
        return True, None
    text = raw_expires_at.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return True, None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed_utc = parsed.astimezone(UTC)
    if parsed_utc <= datetime.now(UTC):
        return True, None
    return False, parsed_utc


def build_and_sign_commit_waivers(
    *,
    sdir: Path,
    session_mgr: StudioSessionManager,
    base_ontology: GraphAOntology,
    delta: OntologyDelta,
    principal_id: str,
    low_confidence_waiver: bool,
    grounding_rationale: str | None,
    explicit_ungrounded: list[str] | str | None,
    proposal_id: str | None,
    submitted_delta_sha256: str | None = None,
) -> list[StewardWaiverRecord]:
    """Assemble cryptographically signed K_waiver records from inline or pre-authorized waivers."""
    sec_mgr = session_mgr.security_manager
    delta_sha = delta.canonical_sha256()
    expected_delta_sha = delta_sha.strip().lower()
    clean_submitted_sha = (submitted_delta_sha256 or "").strip().lower()
    base_sha = delta.base_sha256
    expected_base_sha = (base_sha or "").strip().lower()
    signed_waivers: list[StewardWaiverRecord] = []

    waivers_file = sdir / "waivers.json"
    if waivers_file.is_file():
        try:
            stored_entries = json.loads(waivers_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StudioWorkflowError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt waivers.json in session '{sdir.name}': {exc}",
                status_code=500,
                exit_code=1,
            ) from exc

        raw_entries: list[Any] = []
        if isinstance(stored_entries, list):
            raw_entries = stored_entries
        elif isinstance(stored_entries, dict) and isinstance(stored_entries.get("waivers"), list):
            raw_entries = stored_entries["waivers"]
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            envelope = entry.get("envelope")
            sig = entry.get("hmac_signature")
            if not isinstance(envelope, dict) or not isinstance(sig, str):
                raise StudioWorkflowError(
                    "INVALID_GROUNDING_WAIVER",
                    "Stored steward waiver entry lacks valid cryptographic envelope.",
                    status_code=422,
                    exit_code=4,
                )
            canonical_env = dumps_jcs_bytes(envelope)
            expected_sig = sec_mgr.sign_waiver(canonical_env)
            if not hmac.compare_digest(sig, expected_sig):
                raise StudioWorkflowError(
                    "INVALID_GROUNDING_WAIVER",
                    "Stored steward waiver failed K_waiver cryptographic verification.",
                    status_code=422,
                    exit_code=4,
                )

            raw_exp = envelope.get("expires_at")
            if raw_exp is None and isinstance(entry.get("record"), dict):
                raw_exp = entry["record"].get("expires_at")
            is_expired, parsed_exp = parse_waiver_expires_at(raw_exp)
            if is_expired:
                continue

            env_base = str(envelope.get("base_sha256") or "").strip().lower()
            if (
                not env_base
                or env_base == "0" * 64
                or not expected_base_sha
                or not hmac.compare_digest(env_base, expected_base_sha)
            ):
                continue

            raw_env_prop = envelope.get("proposal_id")
            env_prop = str(raw_env_prop).strip() if raw_env_prop is not None else ""
            if proposal_id is not None:
                if env_prop and env_prop != str(proposal_id):
                    continue
            elif env_prop:
                continue

            auth_role = str(envelope.get("authorized_role") or "").strip()
            auth_by = str(envelope.get("authorized_by") or "").strip()
            if auth_role != "admin" or not auth_by:
                continue

            raw_env_delta_sha = envelope.get("proposal_delta_sha256")
            env_delta_sha = (
                str(raw_env_delta_sha).strip().lower() if raw_env_delta_sha is not None else ""
            )
            if env_delta_sha:
                if env_delta_sha == "0" * 64:
                    continue
                matches_commit = hmac.compare_digest(env_delta_sha, expected_delta_sha)
                matches_sub = bool(clean_submitted_sha) and hmac.compare_digest(
                    env_delta_sha, clean_submitted_sha
                )
                if not (matches_commit or matches_sub):
                    continue

            target_uris = [str(u) for u in (envelope.get("target_concept_uris") or []) if u]
            rationale = str(envelope.get("rationale") or "").strip()
            if not target_uris or len(rationale) < 10:
                continue

            draft_kwargs: dict[str, Any] = {
                "proposal_id": proposal_uuid_from_str(proposal_id or env_prop or delta.delta_id),
                "proposal_delta_sha256": delta_sha,
                "target_concept_uris": target_uris,
                "base_sha256": base_sha,
                "rationale": rationale,
                "authorized_by": auth_by,
                "authorized_role": "admin",
                "signature_or_token": "placeholder",
            }
            if parsed_exp is not None:
                now_dt = datetime.now(UTC)
                if parsed_exp <= now_dt:
                    continue
                draft_kwargs["created_at"] = now_dt
                draft_kwargs["expires_at"] = parsed_exp

            try:
                draft_rec = StewardWaiverRecord(**draft_kwargs)
            except (ValidationError, ValueError):
                continue
            real_sig = sec_mgr.sign_waiver(draft_rec.signing_payload().encode("utf-8"))
            signed_waivers.append(draft_rec.model_copy(update={"signature_or_token": real_sig}))

    if low_confidence_waiver:
        if not grounding_rationale or len(grounding_rationale.strip()) < 10:
            raise StudioWorkflowError(
                "INVALID_GROUNDING_WAIVER",
                "Administrative grounding waiver requires a rationale of at least 10 characters.",
                status_code=422,
                exit_code=4,
            )
        target_uris = normalize_ungrounded_list(explicit_ungrounded)
        if not target_uris:
            target_uris = sorted(collect_touched_concept_uris(base_ontology, delta))
        if target_uris:
            try:
                draft_inline = StewardWaiverRecord(
                    proposal_id=proposal_uuid_from_str(proposal_id or delta.delta_id),
                    proposal_delta_sha256=delta_sha,
                    target_concept_uris=target_uris,
                    base_sha256=base_sha,
                    rationale=grounding_rationale.strip(),
                    authorized_by=principal_id,
                    authorized_role="admin",
                    signature_or_token="placeholder",
                )
            except (ValidationError, ValueError) as exc:
                raise StudioWorkflowError(
                    "INVALID_GROUNDING_WAIVER",
                    f"Invalid grounding waiver specification: {exc}",
                    status_code=422,
                    exit_code=4,
                ) from exc
            inline_sig = sec_mgr.sign_waiver(draft_inline.signing_payload().encode("utf-8"))
            signed_waivers.append(
                draft_inline.model_copy(update={"signature_or_token": inline_sig})
            )

    return signed_waivers


def submit_session_waiver_workflow(
    *,
    session_mgr: StudioSessionManager,
    meta: SessionMetadata,
    sdir: Path,
    payload_dict: dict[str, Any],
    principal_id: str,
    resolve_proposal_fn: Any,
) -> dict[str, Any]:
    """Authorize K_waiver-signed steward waiver and append signed audit record."""
    from opendv_service.studio.proposal_workflows import (
        compute_stored_proposal_delta_sha256,
        normalize_proposal_record,
    )

    auth_role_val = payload_dict.get("authorized_role")
    if auth_role_val is not None and str(auth_role_val).strip() != "admin":
        raise StudioWorkflowError(
            "INVALID_GROUNDING_WAIVER",
            "Waiver authorized_role must be 'admin'.",
            status_code=422,
            exit_code=4,
        )

    rationale = str(payload_dict.get("rationale") or "").strip()
    if len(rationale) < 10:
        raise StudioWorkflowError(
            "INVALID_GROUNDING_WAIVER",
            "Waiver rationale must be at least 10 characters.",
            status_code=422,
            exit_code=4,
        )

    raw_uris = (
        payload_dict.get("target_concept_uris")
        or payload_dict.get("concept_uris")
        or payload_dict.get("ungrounded_concepts")
        or ([payload_dict["concept_uri"]] if payload_dict.get("concept_uri") else [])
    )
    target_uris = sorted({str(u).strip() for u in raw_uris if str(u).strip()})
    if not target_uris:
        raise StudioWorkflowError(
            "INVALID_GROUNDING_WAIVER",
            "Waiver must specify at least one target concept URI.",
            status_code=422,
            exit_code=4,
        )

    prop_id = payload_dict.get("proposal_id")
    loaded_proposal_raw: dict[str, Any] | None = None
    loaded_proposal_norm: dict[str, Any] | None = None
    if prop_id:
        prop_file = resolve_proposal_fn(sdir, prop_id)
        if prop_file.is_file():
            try:
                raw_p = json.loads(prop_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StudioWorkflowError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt proposal record '{prop_id}': {exc}",
                    status_code=500,
                    exit_code=1,
                ) from exc
            if not isinstance(raw_p, dict):
                raise StudioWorkflowError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt proposal record '{prop_id}': expected JSON object.",
                    status_code=500,
                    exit_code=1,
                )
            loaded_proposal_raw = raw_p
            loaded_proposal_norm = normalize_proposal_record(
                raw_p, session_id=meta.session_id, fallback_proposal_id=prop_id
            )

    raw_req_base = str(payload_dict.get("base_sha256") or "").strip()
    if not raw_req_base:
        eff_base_sha = (
            str(loaded_proposal_norm["base_sha256"])
            if (loaded_proposal_norm and loaded_proposal_norm.get("base_sha256"))
            else meta.head_sha256
        )
    else:
        clean_req_base = parse_strong_etag_or_error(raw_req_base, header_name="base_sha256")
        if clean_req_base == "0" * 64:
            raise StudioWorkflowError(
                "INVALID_GROUNDING_WAIVER",
                "Wildcard zero base_sha256 is forbidden for steward waivers.",
                status_code=422,
                exit_code=4,
            )
        allowed_bases = {meta.head_sha256}
        if loaded_proposal_norm and loaded_proposal_norm.get("base_sha256"):
            allowed_bases.add(str(loaded_proposal_norm["base_sha256"]))
        if clean_req_base not in allowed_bases:
            raise StudioWorkflowError(
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"Waiver base_sha256 ('{clean_req_base}') does not match "
                f"session head SHA-256 ('{meta.head_sha256}').",
                status_code=412,
                exit_code=5,
            )
        eff_base_sha = clean_req_base

    raw_req_delta_sha = str(payload_dict.get("proposal_delta_sha256") or "").strip().lower()
    if raw_req_delta_sha:
        if raw_req_delta_sha == "0" * 64 or not re.fullmatch(r"[0-9a-f]{64}", raw_req_delta_sha):
            raise StudioWorkflowError(
                "INVALID_GROUNDING_WAIVER",
                "Waiver proposal_delta_sha256 must be a valid non-zero 64-character hex digest.",
                status_code=422,
                exit_code=4,
            )
        envelope_delta_sha = raw_req_delta_sha
    elif prop_id and loaded_proposal_raw is not None:
        envelope_delta_sha = compute_stored_proposal_delta_sha256(
            sdir=sdir, meta=meta, raw_prop=loaded_proposal_raw, proposal_id=prop_id
        )
    else:
        envelope_delta_sha = ""

    waiver_id_val = payload_dict.get("waiver_id")
    waiver_id_uuid = proposal_uuid_from_str(waiver_id_val) if waiver_id_val else uuid.uuid4()
    proposal_uuid = proposal_uuid_from_str(prop_id)
    record_delta_sha = envelope_delta_sha or ("0" * 64)

    record_kwargs: dict[str, Any] = {
        "waiver_id": waiver_id_uuid,
        "proposal_id": proposal_uuid,
        "proposal_delta_sha256": record_delta_sha,
        "target_concept_uris": target_uris,
        "base_sha256": eff_base_sha,
        "rationale": rationale,
        "authorized_by": principal_id,
        "authorized_role": "admin",
        "signature_or_token": "placeholder",
    }
    if payload_dict.get("created_at") is not None:
        record_kwargs["created_at"] = payload_dict["created_at"]
    if payload_dict.get("expires_at") is not None:
        record_kwargs["expires_at"] = payload_dict["expires_at"]

    try:
        draft_record = StewardWaiverRecord(**record_kwargs)
    except (ValidationError, ValueError) as exc:
        raise StudioWorkflowError(
            "INVALID_GROUNDING_WAIVER",
            f"Invalid steward waiver record: {exc}",
            status_code=422,
            exit_code=4,
        ) from exc

    sec_mgr = session_mgr.security_manager
    waiver_sig = sec_mgr.sign_waiver(draft_record.signing_payload().encode("utf-8"))
    signed_record = draft_record.model_copy(update={"signature_or_token": waiver_sig})

    envelope: dict[str, Any] = {
        "waiver_id": str(signed_record.waiver_id),
        "proposal_id": prop_id,
        "proposal_delta_sha256": envelope_delta_sha,
        "target_concept_uris": target_uris,
        "base_sha256": eff_base_sha,
        "rationale": rationale,
        "authorized_by": principal_id,
        "authorized_role": "admin",
        "created_at": signed_record.created_at.isoformat(),
        "expires_at": signed_record.expires_at.isoformat()
        if signed_record.expires_at is not None
        else None,
    }
    envelope_sig = sec_mgr.sign_waiver(dumps_jcs_bytes(envelope))

    with reentrant_session_lock(sdir / ".session.lock", session_id=sdir.name):
        waivers_file = sdir / "waivers.json"
        existing_waivers: list[dict[str, Any]] = []
        if waivers_file.is_file():
            try:
                loaded_w = json.loads(waivers_file.read_text(encoding="utf-8"))
                if isinstance(loaded_w, list):
                    existing_waivers = [w for w in loaded_w if isinstance(w, dict)]
                elif isinstance(loaded_w, dict) and isinstance(loaded_w.get("waivers"), list):
                    for item in loaded_w["waivers"]:
                        if not isinstance(item, dict):
                            continue
                        if isinstance(item.get("envelope"), dict) and isinstance(
                            item.get("hmac_signature"), str
                        ):
                            rec = item.get("record")
                            if not isinstance(rec, dict):
                                rec = {
                                    k: v
                                    for k, v in item.items()
                                    if k not in ("envelope", "hmac_signature")
                                }
                            existing_waivers.append(
                                {
                                    "envelope": item["envelope"],
                                    "hmac_signature": item["hmac_signature"],
                                    "record": rec,
                                }
                            )
                        else:
                            existing_waivers.append(item)
            except (OSError, json.JSONDecodeError) as exc:
                raise StudioWorkflowError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt waivers.json in session '{meta.session_id}': {exc}",
                    status_code=500,
                    exit_code=1,
                ) from exc

        existing_waivers.append(
            {
                "envelope": envelope,
                "hmac_signature": envelope_sig,
                "record": signed_record.model_dump(mode="json"),
            }
        )
        tmp_w = sdir / f".waivers.{uuid.uuid4().hex[:8]}.tmp"
        tmp_w.write_text(json.dumps(existing_waivers, indent=2), encoding="utf-8")
        replace_with_retry(tmp_w, waivers_file)
        set_owner_only_permissions(waivers_file)

        append_signed_audit_record(
            session_dir=sdir,
            action="STEWARD_WAIVER_AUTHORIZED",
            actor=principal_id,
            details={
                "waiver_id": str(signed_record.waiver_id),
                "target_concept_uris": target_uris,
                "base_sha256": eff_base_sha,
                "rationale": rationale,
                "expires_at": envelope["expires_at"],
            },
            security_manager=sec_mgr,
        )

    return {
        "status": "authorized",
        "waiver_id": str(signed_record.waiver_id),
        "target_concept_uris": target_uris,
        "base_sha256": eff_base_sha,
    }
