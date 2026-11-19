"""6-step atomic commit protocol execution for AtomicCommitService."""

from __future__ import annotations

import inspect
import json
import os
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from opendv_contracts.dtos.ontology import (
    OntologyDelta,
    StewardWaiverRecord,
    is_destructive_delta,
)
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.genesis import GENESIS_VERSION
from opendv_core.ontology_hashing import (
    canonicalize_graph_a_dict,
    compute_ontology_sha256,
    dumps_jcs_bytes,
)
from opendv_service.ontology.applier import apply as apply_delta
from opendv_service.ontology.matching.verifier import get_default_grounding_verifier
from opendv_service.ontology.validator import (
    GovernanceValidator,
    SemVerCalculusError,
    build_workspace_deployment_provider,
)
from opendv_service.studio.atomic_commit_errors import (
    CommitValidationError,
    ConcurrentModificationError,
    ForbiddenAdminRequiredError,
    VersionCommitResult,
)
from opendv_service.studio.atomic_commit_grounding import (
    _AcceptedDeltaWaiverVerifierProxy,
    _compute_required_target_semver,
    _load_grandfathered_concept_uris,
    _LocalWaiverVerifier,
    fsync_directory_best_effort,
    parse_semver_tuple,
    parse_strong_etag,
    verify_ont_evo_002_post_state_grounding,
)
from opendv_service.studio.locks import DEFAULT_LOCK_TIMEOUT_SECONDS, reentrant_session_lock
from opendv_service.studio.session_manager import normalize_version_tag, sanitize_domain_namespace
from opendv_service.studio.workspace_security import replace_with_retry, set_owner_only_permissions


def execute_commit_delta(
    service: Any,
    session_id: str,
    delta: OntologyDelta,
    proposal_id: str | None = None,
    author: str = "steward",
    if_match: str | None = None,
    evaluation: dict[str, Any] | None = None,
    timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    *,
    actor_role: str = "editor",
    governance_validator: GovernanceValidator | None = None,
    waivers: list[StewardWaiverRecord] | None = None,
    grandfathered_concept_uris: set[str] | frozenset[str] | None = None,
) -> VersionCommitResult:
    """Execute the 6-step atomic commit protocol under Total Lock Hierarchy L5 session lock."""
    session_dir = service.session_manager.get_session_dir(session_id)
    if not session_dir.exists() or not (session_dir / "session.json").exists():
        raise FileNotFoundError(f"Session '{session_id}' not found")

    # Validate strong If-Match syntax upfront (HTTP 400 / exit 2 on weak/wildcard/malformed)
    clean_if_match: str | None = None
    if if_match is not None:
        clean_if_match = parse_strong_etag(if_match)

    lock_path = session_dir / ".session.lock"
    with reentrant_session_lock(lock_path, session_id=session_id, timeout=timeout_sec):
        service.reconcile_and_recover_session(session_id)
        session = service.session_manager.get_session(session_id)

        # Step 2a: Strict OCC Precondition Verification (no unanchored 0.0.0 bypass)
        delta_base_sha = (delta.base_sha256 or delta.parent_sha256 or "").strip().lower()
        clean_delta_base_ver = (delta.base_version or "").strip().lstrip("v")
        clean_active_ver = session.active_version.strip().lstrip("v")

        if clean_if_match is not None and clean_if_match != session.head_sha256:
            raise ConcurrentModificationError(
                f"If-Match precondition failed for session '{session_id}': "
                f"header specifies '{clean_if_match}', but session head is "
                f"'{session.head_sha256}'"
            )

        if clean_delta_base_ver != clean_active_ver:
            raise ConcurrentModificationError(
                f"OCC check failed for session '{session_id}': active version is "
                f"'{session.active_version}', but delta specifies base_version "
                f"'{delta.base_version}'"
            )

        if clean_active_ver != GENESIS_VERSION:
            if not delta_base_sha or delta_base_sha != session.head_sha256:
                raise ConcurrentModificationError(
                    f"OCC check failed for session '{session_id}': active head is "
                    f"'{session.head_sha256}', but delta specifies base '{delta_base_sha}'"
                )
        elif delta_base_sha:
            if delta_base_sha != session.head_sha256:
                raise ConcurrentModificationError(
                    f"OCC check failed for session '{session_id}': active head is "
                    f"'{session.head_sha256}', but delta specifies base '{delta_base_sha}'"
                )
        elif clean_if_match is None:
            raise ConcurrentModificationError(
                f"OCC check failed for session '{session_id}': delta base_sha256 is empty "
                f"without an anchoring If-Match matching head '{session.head_sha256}'"
            )

        # Proposal resolution concurrency check
        if proposal_id:
            prop_file = session_dir / "proposals" / f"{proposal_id}.json"
            if prop_file.exists():
                try:
                    curr_prop = json.loads(prop_file.read_text(encoding="utf-8"))
                    if curr_prop.get("status") in ("ACCEPTED", "REJECTED"):
                        raise ConcurrentModificationError(
                            f"Proposal '{proposal_id}' has already been resolved "
                            f"({curr_prop.get('status')})"
                        )
                except ConcurrentModificationError:
                    raise
                except (OSError, json.JSONDecodeError):
                    pass

        if delta.target_ontology_version is not None:
            try:
                req_tuple = parse_semver_tuple(delta.target_ontology_version)
            except ValueError as err:
                raise CommitValidationError(
                    f"Invalid target semantic version '{delta.target_ontology_version}': {err}",
                    error_code=ErrorCode.ONT_EVO_ENVELOPE_VIOLATION,
                    status_code=422,
                    exit_code=4,
                ) from err
            if req_tuple <= parse_semver_tuple(session.head_version):
                raise CommitValidationError(
                    f"Target version '{delta.target_ontology_version}' must exceed active "
                    f"version '{session.head_version}' in SemVer total order",
                    error_code=ErrorCode.ONT_EVO_ENVELOPE_VIOLATION,
                    status_code=422,
                    exit_code=4,
                )

        if not delta.operations:
            raise CommitValidationError(
                "Cannot commit an empty delta with zero operations.",
                error_code=ErrorCode.NO_OP_DELTA,
                status_code=400,
                exit_code=1,
            )

        # Load authoritative base ontology (zero double-apply stub hacks!)
        base_ontology = service._load_version_ontology(session_dir, session, session.head_version)
        if delta.domain_namespace and base_ontology.ontology_version == GENESIS_VERSION:
            clean_delta_ns = sanitize_domain_namespace(delta.domain_namespace)
            if clean_delta_ns != base_ontology.domain_namespace:
                base_ontology = base_ontology.model_copy(
                    update={"domain_namespace": clean_delta_ns}
                )

        # Step 2b: RBAC check — destructive MAJOR deltas require admin role (P10::MAJ-02)
        if is_destructive_delta(delta, base_ontology) and actor_role != "admin":
            raise ForbiddenAdminRequiredError(
                "Destructive (MAJOR SemVer) ontology revisions require administrator role."
            )

        # Step 2c: Exact SemVer calculus verification (P10::MAJ-03)
        if delta.base_ontology_version and delta.base_ontology_version.lstrip(
            "v"
        ) != session.head_version.lstrip("v"):
            raise CommitValidationError(
                f"Delta base_version '{delta.base_ontology_version}' does not match "
                f"session head version '{session.head_version}'.",
                error_code=ErrorCode.ONT_EVO_ENVELOPE_VIOLATION,
                status_code=422,
                exit_code=4,
            )

        try:
            expected_semver = _compute_required_target_semver(
                session.head_version, delta, base_ontology
            )
        except (SemVerCalculusError, ValueError) as err:
            raise CommitValidationError(
                f"Invalid ontology SemVer transition: {err}",
                error_code=ErrorCode.ONT_EVO_ENVELOPE_VIOLATION,
                status_code=422,
                exit_code=4,
            ) from err

        if delta.target_ontology_version is not None and delta.target_ontology_version.lstrip(
            "v"
        ) != expected_semver.lstrip("v"):
            raise CommitValidationError(
                f"Target version '{delta.target_ontology_version}' violates required "
                f"ONT-EVO-001 SemVer bump '{expected_semver}' from '{session.head_version}'.",
                error_code=ErrorCode.ONT_EVO_ENVELOPE_VIOLATION,
                status_code=422,
                exit_code=4,
            )

        # Compute submitted delta digest BEFORE normalization rewrites parent / target_version
        submitted_delta_sha = delta.canonical_sha256()

        # Apply delta exactly once via structural applier
        try:
            normalized_delta = (
                delta
                if (
                    delta.parent_sha256 == session.head_sha256
                    and delta.target_ontology_version == expected_semver
                )
                else delta.model_copy(
                    update={
                        "parent_sha256": session.head_sha256,
                        "target_ontology_version": expected_semver,
                    }
                )
            )
            normalized_delta_sha = normalized_delta.canonical_sha256()
            target_ontology = apply_delta(
                base_ontology, normalized_delta, validate_structural=True
            )
            target_ontology.parent_sha256 = session.head_sha256
        except (SemVerCalculusError, ValidationError, ValueError, OpenDVError) as err:
            if isinstance(err, OpenDVError) and not isinstance(err, SemVerCalculusError):
                raise
            raise CommitValidationError(f"Invalid ontology delta transition: {err}") from err

        # Step 2d: Unconditional GovernanceValidator & ONT-EVO-002 18+2 op grounding enforcement
        effective_grandfathered = (
            set(grandfathered_concept_uris)
            if grandfathered_concept_uris is not None
            else _load_grandfathered_concept_uris(session_dir)
        )
        accepted_delta_shas = (normalized_delta_sha, submitted_delta_sha)
        local_waiver_verifier = _LocalWaiverVerifier(
            service.security_manager, accepted_delta_shas=accepted_delta_shas
        )
        ws_root = getattr(service.session_manager, "deployment_workspace_root", None) or getattr(
            service.session_manager, "workspace_root", None
        )
        gv = governance_validator or service.governance_validator
        if gv is None:
            dep_prov = build_workspace_deployment_provider(ws_root)
            gv = GovernanceValidator(
                deployment_provider=dep_prov,
                waiver_verifier=local_waiver_verifier,
            )
        elif gv.waiver_verifier is None or isinstance(gv.waiver_verifier, _LocalWaiverVerifier):
            dep_prov = getattr(gv, "deployment_provider", None)
            if dep_prov is None:
                dep_prov = build_workspace_deployment_provider(ws_root)
            gv = GovernanceValidator(
                deployment_provider=dep_prov,
                grounding_verifier=gv.grounding_verifier,
                waiver_verifier=local_waiver_verifier,
            )
        else:
            dep_prov = getattr(gv, "deployment_provider", None)
            if dep_prov is None:
                dep_prov = build_workspace_deployment_provider(ws_root)
            gv = GovernanceValidator(
                deployment_provider=dep_prov,
                grounding_verifier=gv.grounding_verifier,
                waiver_verifier=_AcceptedDeltaWaiverVerifierProxy(
                    gv.waiver_verifier, accepted_delta_shas
                ),
            )

        occ_kwargs: dict[str, Any] = {
            "session_head_sha256": session.head_sha256,
            "if_match": if_match,
        }
        occ_sig = inspect.signature(gv.validate_occ)
        if "actual_base_version" in occ_sig.parameters:
            occ_kwargs["actual_base_version"] = session.active_version
        try:
            gv.validate_occ(normalized_delta, **occ_kwargs)
        except OpenDVError as occ_err:
            if occ_err.error_code == ErrorCode.ONT_EVO_CONCURRENT_MODIFICATION:
                raise ConcurrentModificationError(str(occ_err)) from occ_err
            raise

        gv.validate_deployed_contracts(base_ontology, normalized_delta)
        gv.validate_grounding(
            base_ontology,
            normalized_delta,
            target_ontology,
            waivers=waivers,
            grandfathered_concept_uris=effective_grandfathered,
        )
        verify_ont_evo_002_post_state_grounding(
            base_ontology,
            normalized_delta,
            target_ontology,
            waivers=waivers,
            security_manager=service.security_manager,
            grounding_verifier=gv.grounding_verifier or get_default_grounding_verifier(),
            submitted_delta_sha256=submitted_delta_sha,
        )

        target_version_raw = target_ontology.ontology_version.lstrip("v")
        target_version_tag = normalize_version_tag(target_version_raw)

        if parse_semver_tuple(target_version_raw) <= parse_semver_tuple(session.head_version):
            raise CommitValidationError(
                f"Target version '{target_version_raw}' must exceed active version "
                f"'{session.head_version}' in SemVer total order"
            )

        # Canonical RFC 8785 JCS bytes: sha256(ontology.json) == target_sha256 bit-for-bit!
        canonical_dict = canonicalize_graph_a_dict(target_ontology)
        canonical_ont_bytes = dumps_jcs_bytes(canonical_dict)
        target_sha256 = compute_ontology_sha256(target_ontology)

        # Step 3: Write-ahead log PENDING_COMMIT entry signed via K_audit
        now_iso = datetime.now(UTC).isoformat()
        pending_audit_details: dict[str, Any] = {
            "target_version": target_version_tag,
            "target_sha256": target_sha256,
            "parent_sha256": session.head_sha256,
            "proposal_id": proposal_id,
        }
        pending_entry = service._append_raw_audit_entry_under_lock(
            session_dir=session_dir,
            session_id=session_id,
            action="PENDING_COMMIT",
            target_version=target_version_tag,
            head_sha256=target_sha256,
            actor=author,
            details=pending_audit_details,
            prev_audit_sha=session.audit_chain_head_sha256,
        )
        session.audit_chain_head_sha256 = pending_entry["audit_sha256"]

        # Step 4: Stage version artifacts in .staging-<u>/
        staging_token = secrets.token_hex(6)
        staging_dir = session_dir / f".staging-{staging_token}"
        staging_dir.mkdir(parents=True, exist_ok=True)
        set_owner_only_permissions(staging_dir)

        try:
            ont_json_path = staging_dir / "ontology.json"
            ont_json_path.write_bytes(canonical_ont_bytes)
            set_owner_only_permissions(ont_json_path)

            ont_yaml_path = staging_dir / "ontology.yaml"
            yaml_str = yaml.safe_dump(canonical_dict, sort_keys=False)
            ont_yaml_path.write_text(yaml_str, encoding="utf-8")
            set_owner_only_permissions(ont_yaml_path)

            delta_json_path = staging_dir / "delta.json"
            delta_json_path.write_text(
                normalized_delta.model_dump_json(indent=2), encoding="utf-8"
            )
            set_owner_only_permissions(delta_json_path)

            meta_record: dict[str, Any] = {
                "version": target_version_tag,
                "target_version": target_version_tag,
                "target_sha256": target_sha256,
                "parent_sha256": session.head_sha256,
                "delta_sha256": normalized_delta_sha,
                "submitted_delta_sha256": submitted_delta_sha,
                "proposal_id": proposal_id,
                "created_at": now_iso,
                "author": author,
                "audit_chain_head_sha256": session.audit_chain_head_sha256,
                "domain_namespace": target_ontology.domain_namespace,
                "enterprise_owner": target_ontology.enterprise_owner,
            }
            if waivers:
                meta_record["waivers"] = [
                    w.model_dump(mode="json", exclude_none=True) for w in waivers
                ]
            meta_text = json.dumps(meta_record, indent=2)
            meta_json_path = staging_dir / "meta.json"
            meta_json_path.write_text(meta_text, encoding="utf-8")
            set_owner_only_permissions(meta_json_path)

            metadata_compat_path = staging_dir / "metadata.json"
            metadata_compat_path.write_text(meta_text, encoding="utf-8")
            set_owner_only_permissions(metadata_compat_path)

            eval_payload = dict(evaluation or {})
            if waivers and "waivers" not in eval_payload:
                eval_payload["waivers"] = [
                    w.model_dump(mode="json", exclude_none=True) for w in waivers
                ]
            eval_json_path = staging_dir / "evaluation.json"
            eval_json_path.write_text(json.dumps(eval_payload, indent=2), encoding="utf-8")
            set_owner_only_permissions(eval_json_path)

            staged_files: list[Path] = [
                ont_json_path,
                ont_yaml_path,
                delta_json_path,
                meta_json_path,
                metadata_compat_path,
                eval_json_path,
            ]
            if waivers:
                waivers_json_path = staging_dir / "waivers.json"
                waivers_json_path.write_text(
                    json.dumps(
                        [w.model_dump(mode="json", exclude_none=True) for w in waivers],
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                set_owner_only_permissions(waivers_json_path)
                staged_files.append(waivers_json_path)

            for fpath in staged_files:
                with fpath.open("r+b") as fp:
                    fp.flush()
                    os.fsync(fp.fileno())

            versions_parent = session_dir / "versions"
            versions_parent.mkdir(parents=True, exist_ok=True)
            target_version_dir = versions_parent / target_version_tag

            replace_with_retry(staging_dir, target_version_dir)
            fsync_directory_best_effort(versions_parent)
        finally:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)

        # Step 5: Atomic metadata commit on session.json (The Commit Point)
        committed_list = [normalize_version_tag(v) for v in session.committed_versions]
        if target_version_tag not in committed_list:
            committed_list.append(target_version_tag)

        updated_metadata = session.model_copy(
            update={
                "updated_at": now_iso,
                "head_version": target_version_raw,
                "head_sha256": target_sha256,
                "domain_namespace": target_ontology.domain_namespace,
                "committed_versions": committed_list,
            }
        )
        service._write_session_json(session_dir, updated_metadata)

        # Step 6: Post-commit proposal ACCEPTED/STALE transition + signed COMMITTED audit record
        service._mark_proposals_post_commit(
            session_dir=session_dir,
            accepted_proposal_id=proposal_id,
            target_sha256=target_sha256,
            now_iso=now_iso,
            evaluation=evaluation,
            accepted_by=author,
            waivers=waivers,
        )

        committed_audit_details: dict[str, Any] = {
            "target_version": target_version_tag,
            "target_sha256": target_sha256,
            "parent_sha256": session.head_sha256,
            "proposal_id": proposal_id,
        }
        committed_entry = service._append_raw_audit_entry_under_lock(
            session_dir=session_dir,
            session_id=session_id,
            action="COMMITTED",
            target_version=target_version_tag,
            head_sha256=target_sha256,
            actor=author,
            details=committed_audit_details,
            prev_audit_sha=session.audit_chain_head_sha256,
        )
        final_audit_head = committed_entry["audit_sha256"]

        updated_metadata.audit_chain_head_sha256 = final_audit_head
        service._write_session_json(session_dir, updated_metadata)

        return VersionCommitResult(
            version=target_version_tag,
            target_sha256=target_sha256,
            parent_sha256=session.head_sha256,
            audit_head_sha256=final_audit_head,
            committed_at=now_iso,
            proposal_id=proposal_id,
        )
