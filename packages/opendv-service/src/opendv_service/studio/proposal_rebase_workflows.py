"""Shared Studio proposal rebase and iterative refinement workflows."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from opendv_contracts.dtos.ontology import OntologyDelta
from opendv_contracts.errors import OpenDVError
from opendv_core.genesis import GENESIS_VERSION
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.construction.deterministic_pipeline import DeterministicConstructionPipeline
from opendv_service.construction.models import ConstructOptions
from opendv_service.ontology.rebase import check_rebase_conflicts
from opendv_service.ontology.validator import (
    GovernanceValidator,
    build_workspace_deployment_provider,
)
from opendv_service.studio.atomic_commit import _load_grandfathered_concept_uris, parse_semver_tuple
from opendv_service.studio.delta_normalizer import (
    StudioWorkflowError,
    compute_expected_semver_for_ops,
    load_version_ontology_fail_closed,
    normalize_delta_operation_wire,
    parse_strong_etag_or_error,
    raise_from_opendv_error,
)
from opendv_service.studio.job_runners import (
    _prepare_evidence_for_agent,
    load_active_session_evidence,
    load_binding_clarification_answers,
)
from opendv_service.studio.locks import reentrant_session_lock
from opendv_service.studio.proposal_helpers import (
    normalize_proposal_record,
    read_proposal_json_safe,
    resolve_proposal_file_safe,
    write_proposal_json_atomic,
)
from opendv_service.studio.session_manager import (
    SessionMetadata,
    StudioSessionManager,
    append_signed_audit_record,
)


def collect_intervening_deltas(
    sdir: Path,
    meta: SessionMetadata,
    prop_base_version: str,
    prop_base_sha256: str,
    base_ontology_sha256: str,
) -> list[OntologyDelta]:
    """Load and cryptographically verify intervening deltas from prop_base up to HEAD."""
    clean_base_ver = str(prop_base_version or "").strip().lstrip("v") or GENESIS_VERSION
    clean_head_ver = str(meta.head_version or "").strip().lstrip("v")
    try:
        base_tup = parse_semver_tuple(clean_base_ver)
        head_tup = parse_semver_tuple(clean_head_ver)
    except ValueError as exc:
        raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Malformed SemVer version during rebase: {exc}", status_code=500, exit_code=1) from exc

    if base_tup > head_tup:
        raise StudioWorkflowError("PROPOSAL_STALE_REBASE_REQUIRED", f"Proposal base_version '{prop_base_version}' exceeds session head_version '{meta.head_version}'.", status_code=409, exit_code=6)

    expected_head_sha = str(meta.head_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_head_sha):
        raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Corrupt session head_sha256 in session '{meta.session_id}'.", status_code=500, exit_code=1)

    eff_prop_base_sha = str(prop_base_sha256 or "").strip().lower()
    if eff_prop_base_sha:
        if not re.fullmatch(r"[0-9a-f]{64}", eff_prop_base_sha):
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Malformed proposal base_sha256 '{prop_base_sha256}'.", status_code=500, exit_code=1)
    elif clean_base_ver == GENESIS_VERSION:
        eff_prop_base_sha = base_ontology_sha256.strip().lower()
    else:
        raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Empty proposal base_sha256 for non-genesis base_version '{prop_base_version}'.", status_code=500, exit_code=1)

    versions_dir = sdir / "versions"
    if not versions_dir.is_dir():
        if base_tup < head_tup or meta.committed_versions:
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Missing versions directory in session '{meta.session_id}'.", status_code=500, exit_code=1)
        if eff_prop_base_sha != expected_head_sha:
            raise StudioWorkflowError("PROPOSAL_STALE_REBASE_REQUIRED", f"Proposal base SHA-256 ('{eff_prop_base_sha}') is not an ancestor of session head ('{meta.head_sha256}').", status_code=409, exit_code=6)
        return []

    dir_by_tuple: dict[tuple[int, int, int], Path] = {}
    for vdir in sorted(versions_dir.iterdir()):
        if not vdir.is_dir():
            continue
        if not vdir.name.startswith("v") or len(vdir.name) < 2:
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Malformed version directory '{vdir.name}'.", status_code=500, exit_code=1)
        try:
            v_tup = parse_semver_tuple(vdir.name[1:])
        except ValueError as exc:
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Malformed SemVer directory '{vdir.name}': {exc}", status_code=500, exit_code=1) from exc
        if v_tup in dir_by_tuple:
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Duplicate SemVer version directory '{vdir.name}'.", status_code=500, exit_code=1)
        dir_by_tuple[v_tup] = vdir

    for cv in meta.committed_versions:
        try:
            cv_tup = parse_semver_tuple(str(cv or "").strip().lstrip("v"))
        except ValueError as exc:
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Malformed committed_versions entry '{cv}': {exc}", status_code=500, exit_code=1) from exc
        if base_tup < cv_tup <= head_tup and cv_tup not in dir_by_tuple:
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Missing version directory for committed version '{cv}'.", status_code=500, exit_code=1)

    if base_tup == head_tup:
        if eff_prop_base_sha != expected_head_sha:
            raise StudioWorkflowError("PROPOSAL_STALE_REBASE_REQUIRED", f"Proposal base SHA-256 ('{eff_prop_base_sha}') does not match session head ('{meta.head_sha256}').", status_code=409, exit_code=6)
        return []

    collected: list[tuple[tuple[int, int, int], OntologyDelta, str, str]] = []
    for v_tup in sorted(dir_by_tuple):
        if not (base_tup < v_tup <= head_tup):
            continue
        vdir = dir_by_tuple[v_tup]
        delta_file = vdir / "delta.json"
        if not delta_file.is_file():
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Missing delta.json for version '{vdir.name}'.", status_code=500, exit_code=1)
        try:
            d = OntologyDelta.model_validate_json(delta_file.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Corrupt delta.json for '{vdir.name}': {exc}", status_code=500, exit_code=1) from exc
        meta_file = vdir / "meta.json" if (vdir / "meta.json").is_file() else vdir / "metadata.json"
        if not meta_file.is_file():
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Missing meta.json for '{vdir.name}'.", status_code=500, exit_code=1)
        try:
            vmeta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Corrupt {meta_file.name} for '{vdir.name}': {exc}", status_code=500, exit_code=1) from exc
        v_parent_sha = str(vmeta.get("parent_sha256") or "").strip().lower()
        v_target_sha = str(vmeta.get("target_sha256") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", v_parent_sha) or not re.fullmatch(r"[0-9a-f]{64}", v_target_sha):
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Corrupt SHA-256 lineage metadata in '{vdir.name}'.", status_code=500, exit_code=1)
        try:
            d_target_tup = parse_semver_tuple((d.target_version or "").lstrip("v"))
        except ValueError as exc:
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Corrupt delta target_version in '{vdir.name}': {exc}", status_code=500, exit_code=1) from exc
        if d_target_tup != v_tup or ((d.parent_sha256 or "").strip().lower() and (d.parent_sha256 or "").strip().lower() != v_parent_sha):
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Delta lineage mismatch in '{vdir.name}'.", status_code=500, exit_code=1)
        v_ont = load_version_ontology_fail_closed(sdir, meta, vdir.name.lstrip("v"))
        if compute_ontology_sha256(v_ont).lower() != v_target_sha:
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Ontology digest mismatch for '{vdir.name}'.", status_code=500, exit_code=1)
        collected.append((v_tup, d, v_parent_sha, v_target_sha))

    if not collected or eff_prop_base_sha != base_ontology_sha256.strip().lower() or collected[0][2] != eff_prop_base_sha:
        is_stale = eff_prop_base_sha != base_ontology_sha256.strip().lower()
        raise StudioWorkflowError("PROPOSAL_STALE_REBASE_REQUIRED" if is_stale else "CORRUPT_SESSION_RECORD", "Broken lineage during rebase.", status_code=409 if is_stale else 500, exit_code=6 if is_stale else 1)

    prev_sha = collected[0][3]
    for _v_tup, _d, vp, vt in collected[1:]:
        if vp != prev_sha:
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", "Broken SHA-256 lineage chain.", status_code=500, exit_code=1)
        prev_sha = vt
    if collected[-1][0] != head_tup or prev_sha != expected_head_sha:
        raise StudioWorkflowError("CORRUPT_SESSION_RECORD", "Terminal version lineage does not match session head_sha256.", status_code=500, exit_code=1)
    return [d for _, d, _, _ in collected]


def rebase_session_proposal_workflow(
    *,
    session_mgr: StudioSessionManager,
    sdir: Path,
    session_id: str,
    proposal_id: str,
    if_match: str | None,
    base_sha256_req: str | None,
    principal_id: str,
) -> dict[str, Any]:
    clean_if_match_hdr = parse_strong_etag_or_error(if_match) if if_match is not None else None
    clean_body_sha = parse_strong_etag_or_error(base_sha256_req, header_name="base_sha256") if base_sha256_req else None
    if clean_if_match_hdr is None and clean_body_sha is None:
        raise StudioWorkflowError("MISSING_PRECONDITION_HEADER", "Rebasing a proposal requires an 'If-Match' header anchoring session head SHA-256.", status_code=400, exit_code=2)
    if clean_if_match_hdr is not None and clean_body_sha is not None and clean_if_match_hdr != clean_body_sha:
        raise StudioWorkflowError("PRECONDITION_BASE_SHA_MISMATCH", f"If-Match header ('{clean_if_match_hdr}') does not match body base_sha256 ('{clean_body_sha}').", status_code=412, exit_code=5)
    clean_if_match = clean_if_match_hdr or clean_body_sha

    prop_file = resolve_proposal_file_safe(sdir, proposal_id)
    if not prop_file.is_file():
        raise StudioWorkflowError("PROPOSAL_NOT_FOUND", f"Proposal '{proposal_id}' not found.", status_code=404, exit_code=3)

    with reentrant_session_lock(sdir / ".session.lock", session_id=sdir.name):
        prop_data = normalize_proposal_record(read_proposal_json_safe(prop_file, proposal_id), session_id=session_id, fallback_proposal_id=proposal_id)
        if prop_data.get("status") in ("ACCEPTED", "REJECTED", "SUPERSEDED"):
            raise StudioWorkflowError("PROPOSAL_ALREADY_RESOLVED", f"Proposal '{proposal_id}' has already been resolved ({prop_data.get('status')}).", status_code=409, exit_code=6)
        meta = session_mgr.get_session(session_id)
        if clean_if_match != meta.head_sha256:
            raise StudioWorkflowError("ONT_EVO_CONCURRENT_MODIFICATION", f"If-Match ('{clean_if_match}') does not match session head SHA-256 ('{meta.head_sha256}').", status_code=412, exit_code=5)

        prop_base_ver = str(prop_data.get("base_version") or GENESIS_VERSION).strip().lstrip("v") or GENESIS_VERSION
        prop_base_sha = str(prop_data.get("base_sha256") or "")
        try:
            base_tup = parse_semver_tuple(prop_base_ver)
            head_tup = parse_semver_tuple(str(meta.head_version or "").strip().lstrip("v"))
        except ValueError as exc:
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Malformed SemVer version during rebase: {exc}", status_code=500, exit_code=1) from exc
        if base_tup > head_tup:
            raise StudioWorkflowError("PROPOSAL_STALE_REBASE_REQUIRED", f"Proposal base_version '{prop_base_ver}' exceeds session head_version '{meta.head_version}'.", status_code=409, exit_code=6)

        ops = [normalize_delta_operation_wire(op) for op in prop_data.get("operations", [])]
        base_ontology = load_version_ontology_fail_closed(sdir, meta, prop_base_ver)
        head_ontology = load_version_ontology_fail_closed(sdir, meta, meta.head_version)
        base_ont_sha = compute_ontology_sha256(base_ontology)
        if compute_ontology_sha256(head_ontology).lower() != str(meta.head_sha256 or "").strip().lower():
            raise StudioWorkflowError("CORRUPT_SESSION_RECORD", "Head ontology SHA-256 does not match session.json head_sha256.", status_code=500, exit_code=1)

        intervening_deltas = collect_intervening_deltas(sdir, meta, prop_base_ver, prop_base_sha, base_ont_sha)
        eff_prop_parent = prop_base_sha.strip().lower() or base_ont_sha
        try:
            orig_t_ver = compute_expected_semver_for_ops(prop_base_ver, eff_prop_parent, ops, base_ontology, meta.domain_namespace)
            prop_delta = OntologyDelta(
                delta_id=f"delta-rebase-{uuid.uuid4().hex[:8]}",
                parent_sha256=eff_prop_parent,
                base_version=prop_base_ver,
                target_version=orig_t_ver,
                domain_namespace=meta.domain_namespace,
                operations=ops,
                author=prop_data.get("proposer", "unknown"),
            )
        except OpenDVError as exc:
            raise raise_from_opendv_error(exc) from exc
        except (ValidationError, ValueError, TypeError) as exc:
            raise StudioWorkflowError("ONT_EVO_CONTRACT_VIOLATION", f"Rebase delta structural integrity check failed: {exc}", status_code=422, exit_code=4) from exc

        rebase_res = check_rebase_conflicts(
            proposal_ops=prop_delta,
            base_ontology=base_ontology,
            head_ontology=head_ontology,
            intervening_deltas=intervening_deltas,
            grandfathered_concept_uris=_load_grandfathered_concept_uris(sdir),
            raise_on_conflict=False,
        )
        if rebase_res.has_conflicts:
            first_reason = rebase_res.conflicts[0].reason if rebase_res.conflicts else "Proposal conflicts with concurrent modifications in HEAD."
            raise StudioWorkflowError("PROPOSAL_STALE_REBASE_REQUIRED", first_reason, status_code=409, exit_code=6, details={"conflicts": [c.to_dict() for c in rebase_res.conflicts]})

        try:
            dep_prov = build_workspace_deployment_provider(getattr(session_mgr, "deployment_workspace_root", None) or getattr(session_mgr, "workspace_root", None))
            if dep_prov is not None and rebase_res.rebased_delta is not None:
                GovernanceValidator(deployment_provider=dep_prov, strict_verifier_required=False).validate_deployed_contracts(head_ontology, rebase_res.rebased_delta)
        except OpenDVError as exc:
            raise raise_from_opendv_error(exc) from exc

        rebased_ops = [op.model_dump(mode="json", exclude_none=True) for op in rebase_res.rebased_delta.operations] if rebase_res.rebased_delta is not None else ops
        prop_data.update({"base_sha256": meta.head_sha256, "base_version": meta.head_version.lstrip("v"), "status": "PENDING", "operations": rebased_ops})
        if "base_ontology_sha256" in prop_data:
            prop_data["base_ontology_sha256"] = meta.head_sha256
        if isinstance(prop_data.get("delta"), dict):
            rebased_target = compute_expected_semver_for_ops(meta.head_version, meta.head_sha256, rebased_ops, head_ontology, meta.domain_namespace)
            cleaned = {k: v for k, v in prop_data["delta"].items() if k not in ("base_sha256", "base_version", "target_version")}
            prop_data["delta"] = {
                **cleaned,
                "parent_sha256": meta.head_sha256,
                "base_ontology_version": meta.head_version.lstrip("v"),
                "target_ontology_version": rebased_target,
                "operations": rebased_ops,
            }
        write_proposal_json_atomic(prop_file, prop_data)
        append_signed_audit_record(
            session_dir=sdir,
            action="PROPOSAL_REBASED",
            actor=principal_id,
            details={"proposal_id": proposal_id, "previous_base_version": prop_base_ver, "new_base_version": meta.head_version, "new_base_sha256": meta.head_sha256},
            security_manager=session_mgr.security_manager,
        )
    return prop_data


def refine_session_proposal_workflow(
    *,
    session_mgr: StudioSessionManager,
    meta: SessionMetadata,
    sdir: Path,
    proposal_id: str,
    steering_prompt: str,
    mode: str,
    principal_id: str,
    agent_runner: Any,
) -> dict[str, Any]:
    prop_file = resolve_proposal_file_safe(sdir, proposal_id)
    if not prop_file.is_file():
        raise StudioWorkflowError("PROPOSAL_NOT_FOUND", f"Proposal '{proposal_id}' not found.", status_code=404, exit_code=3)

    with reentrant_session_lock(sdir / ".session.lock", session_id=sdir.name):
        prop_data = normalize_proposal_record(read_proposal_json_safe(prop_file, proposal_id), session_id=meta.session_id, fallback_proposal_id=proposal_id)
        if prop_data.get("status") in ("ACCEPTED", "REJECTED", "SUPERSEDED"):
            raise StudioWorkflowError("PROPOSAL_ALREADY_RESOLVED", f"Cannot refine proposal '{proposal_id}' because its status is '{prop_data.get('status')}'. Only PENDING proposals can be refined.", status_code=409, exit_code=6)

        base_version = prop_data.get("base_version") or meta.head_version
        base_sha256 = prop_data.get("base_sha256") or meta.head_sha256
        chunks, tables = load_active_session_evidence(sdir)
        answers_dict = load_binding_clarification_answers(sdir)
        base_ont = load_version_ontology_fail_closed(sdir, meta, base_version)

        clean_mode = mode.strip().lower() if mode else "auto"
        if clean_mode in ("auto", "agent") and agent_runner is not None:
            agent_chunks, agent_tables = _prepare_evidence_for_agent(chunks, tables, session_mgr.security_manager)
            refined_proposal = agent_runner(
                base_ontology=base_ont,
                evidence_chunks=agent_chunks,
                tables=agent_tables,
                clarification_answers=answers_dict,
                session_dir=sdir,
                steering_prompt=steering_prompt,
                job_id=f"refine-{proposal_id[:8]}",
                principal=principal_id,
                mode=clean_mode,
            )
        else:
            opts = ConstructOptions(mode="deterministic", session_dir=sdir, actor=principal_id, principal=principal_id, steering_prompt=steering_prompt)
            refined_proposal = DeterministicConstructionPipeline().construct_proposal(
                base_ontology=base_ont,
                evidence_chunks=chunks,
                tables=tables,
                clarification_answers=answers_dict,
                options=opts,
            )

        refined_proposal_id = f"prop-refine-{uuid.uuid4().hex[:8]}"
        now_iso = datetime.now(UTC).isoformat()
        steering_history = [*list(prop_data.get("steering_history") or []), steering_prompt]
        prop_data.update({"status": "SUPERSEDED", "superseded_by": refined_proposal_id, "updated_at": now_iso})
        write_proposal_json_atomic(prop_file, prop_data)

        refined_record = {
            "proposal_id": refined_proposal_id,
            "session_id": meta.session_id,
            "base_version": base_version,
            "base_sha256": base_sha256,
            "status": "PENDING",
            "operations": [op.model_dump(mode="json", exclude_none=True) for op in refined_proposal.delta.operations],
            "proposer": principal_id,
            "created_at": now_iso,
            "rationale": f"Refinement of '{proposal_id}'. Steering: '{steering_prompt}'.",
            "delta": refined_proposal.delta.model_dump(mode="json", exclude_none=True),
            "replaces_proposal_id": proposal_id,
            "steering_history": steering_history,
        }
        write_proposal_json_atomic(sdir / "proposals" / f"{refined_proposal_id}.json", refined_record)
        append_signed_audit_record(
            session_dir=sdir,
            action="PROPOSAL_REFINED",
            actor=principal_id,
            details={"previous_proposal_id": proposal_id, "refined_proposal_id": refined_proposal_id, "steering_prompt": steering_prompt},
            security_manager=session_mgr.security_manager,
        )
        return refined_record
