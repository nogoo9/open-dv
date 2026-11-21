"""Two-phase atomic workspace ontology promotion under L4 -> L5 locks."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from opendv_contracts.errors import ErrorCode
from opendv_core.genesis import GENESIS_VERSION
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import (
    canonicalize_graph_a_dict,
    compute_ontology_sha256,
    dumps_jcs_bytes,
)
from opendv_service.studio.atomic_commit_audit import write_promotion_record
from opendv_service.studio.atomic_commit_errors import (
    CommitValidationError,
    ConcurrentModificationError,
    ForbiddenAdminRequiredError,
    HistoricalContractRationaleRequiredError,
    HistoricalContractWarningUnacknowledgedError,
    LineageRootMismatchError,
    NoOpPromotionError,
    PromotionRationaleRequiredError,
    PromotionResult,
    atomic_write_bytes,
)
from opendv_service.studio.atomic_commit_grounding import (
    fsync_directory_best_effort,
    parse_semver_tuple,
    parse_strong_etag,
)
from opendv_service.studio.locks import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    LockLevel,
    StudioLock,
    multi_studio_lock,
    reentrant_session_lock,
)
from opendv_service.studio.session_manager import normalize_version_tag
from opendv_service.studio.workspace_security import (
    InvalidWorkspacePathError,
    validate_workspace_containment,
)


def collect_session_lineage_shas(session_dir: Path) -> set[str]:
    """Collect all ontology SHA-256 digests recorded in session versions/."""
    shas: set[str] = set()
    versions_dir = session_dir / "versions"
    if not versions_dir.is_dir():
        return shas
    for vdir in versions_dir.iterdir():
        if not vdir.is_dir():
            continue
        for mname in ("meta.json", "metadata.json"):
            mpath = vdir / mname
            if mpath.is_file():
                with contextlib.suppress(OSError, json.JSONDecodeError):
                    mdata = json.loads(mpath.read_text(encoding="utf-8"))
                    t_sha = mdata.get("target_sha256")
                    if isinstance(t_sha, str) and len(t_sha) == 64:
                        shas.add(t_sha.lower())
    return shas


def execute_promote_to_workspace(
    service: Any,
    session_id: str,
    target_workspace: Path | str,
    expected_workspace_sha256: str,
    *,
    expected_head_sha256: str | None = None,
    if_match: str | None = None,
    rationale: str = "",
    actor: str = "admin",
    actor_role: str = "editor",
    acknowledge_historical_contract_warning: bool = False,
    historical_contract_rationale: str | None = None,
    allowed_workspace_roots: Sequence[Path | str] | None = None,
    is_remote_request: bool = False,
    enforce_lineage: bool = True,
    allow_noop: bool = False,
    timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> PromotionResult:
    """Execute two-phase atomic workspace ontology promotion under L4 -> L5 locks."""
    if actor_role != "admin":
        raise ForbiddenAdminRequiredError(
            "Promoting session ontology to workspace requires administrator role."
        )

    if not rationale or len(rationale.strip()) < 10:
        raise PromotionRationaleRequiredError(
            "Promotion rationale must be at least 10 non-whitespace characters."
        )

    clean_hist_rationale = (historical_contract_rationale or "").strip()
    if acknowledge_historical_contract_warning and len(clean_hist_rationale) < 10:
        raise HistoricalContractRationaleRequiredError(
            "Historical contract rationale must be at least 10 characters."
        )

    clean_if_match: str | None = None
    if if_match is not None:
        clean_if_match = parse_strong_etag(if_match)

    raw_target_ws = Path(target_workspace)
    if raw_target_ws.is_symlink():
        raise InvalidWorkspacePathError(
            f"Workspace promotion target '{raw_target_ws.name}' must not be a symlink."
        )

    resolved_ws = validate_workspace_containment(
        target_workspace,
        allowed_roots=allowed_workspace_roots,
        is_remote_request=is_remote_request,
    )

    if resolved_ws.suffix.lower() in (".yaml", ".yml", ".json"):
        target_contract_path = resolved_ws
        lock_dir = resolved_ws.parent
    else:
        lock_dir = resolved_ws
        target_contract_path = lock_dir / "graph_a_ontology.yaml"
        for candidate_name in (
            "graph_a_ontology.yaml",
            "graph_a.yaml",
            "ontology.yaml",
            "ontology.json",
        ):
            cand = lock_dir / candidate_name
            if cand.exists() or cand.is_symlink():
                target_contract_path = cand
                break

    if target_contract_path.is_symlink() or lock_dir.is_symlink():
        raise InvalidWorkspacePathError(
            f"Workspace contract path '{target_contract_path.name}' must not be a symlink."
        )

    session_dir = service.session_manager.get_session_dir(session_id)
    if not session_dir.exists() or not (session_dir / "session.json").exists():
        raise FileNotFoundError(f"Session '{session_id}' not found")

    lock_dir.mkdir(parents=True, exist_ok=True)
    ws_lock = StudioLock.workspace(
        lock_dir,
        resource_id=os.path.normcase(str(lock_dir)),
        timeout=timeout_sec,
    )
    lifecycle_lock = StudioLock(
        session_dir.parent / f".lifecycle_{session_id}.lock",
        level=LockLevel.L5,
        session_id=session_id,
        timeout=timeout_sec,
        remove_on_release=False,
    )

    with contextlib.ExitStack() as lock_stack:
        lock_stack.enter_context(
            multi_studio_lock([ws_lock, lifecycle_lock], timeout=timeout_sec)
        )
        if not session_dir.exists() or not (session_dir / "session.json").is_file():
            raise FileNotFoundError(
                f"Session directory or session.json not found for '{session_id}'"
            )
        lock_stack.enter_context(
            reentrant_session_lock(
                session_dir / ".session.lock",
                session_id=session_id,
                timeout=timeout_sec,
            )
        )
        # Re-verify containment and symlink absence under locks (TOCTOU guard)
        if (
            raw_target_ws.is_symlink()
            or lock_dir.is_symlink()
            or target_contract_path.is_symlink()
        ):
            raise InvalidWorkspacePathError(
                f"Workspace contract path '{target_contract_path.name}' must not be a symlink."
            )
        validate_workspace_containment(
            target_contract_path,
            allowed_roots=allowed_workspace_roots,
            is_remote_request=is_remote_request,
        )

        service.reconcile_and_recover_session(session_id)
        session = service.session_manager.get_session(session_id)

        if clean_if_match is not None and clean_if_match != session.head_sha256:
            raise ConcurrentModificationError(
                f"If-Match precondition '{clean_if_match}' does not match session head "
                f"'{session.head_sha256}'."
            )

        if expected_head_sha256 is not None:
            clean_exp_head = expected_head_sha256.strip().strip('"').lower()
            if clean_exp_head != session.head_sha256:
                raise ConcurrentModificationError(
                    f"expected_head_sha256 '{clean_exp_head}' does not match session head "
                    f"'{session.head_sha256}'."
                )

        if session.head_version == GENESIS_VERSION:
            raise CommitValidationError(
                "Cannot promote greenfield GenesisDraft (v0.0.0) without committed concepts.",
                error_code=ErrorCode.ONTOLOGY_REQUIRES_CONCEPT,
                status_code=422,
                exit_code=4,
            )

        head_ont = service._load_version_ontology(session_dir, session, session.head_version)
        if not head_ont.canonical_business_concepts:
            raise CommitValidationError(
                "Cannot promote ontology with zero canonical business concepts.",
                error_code=ErrorCode.ONTOLOGY_REQUIRES_CONCEPT,
                status_code=422,
                exit_code=4,
            )

        computed_head_sha = compute_ontology_sha256(head_ont)
        if computed_head_sha != session.head_sha256:
            raise CommitValidationError(
                "Head ontology digest mismatch against session metadata.",
                error_code=ErrorCode.CORRUPT_SESSION_STATE,
                status_code=500,
                exit_code=1,
            )

        # Inspect existing workspace contract for OCC, downgrade warning & lineage
        exp_ws = (expected_workspace_sha256 or "").strip().strip('"').lower()
        pre_ws_canonical_sha = "0" * 64
        existing_ws_ont: GraphAOntology | None = None

        if target_contract_path.is_file():
            raw_ws_bytes = target_contract_path.read_bytes()
            raw_ws_sha = hashlib.sha256(raw_ws_bytes).hexdigest().lower()
            try:
                if target_contract_path.suffix.lower() == ".json":
                    existing_ws_ont = GraphAOntology.model_validate_json(
                        raw_ws_bytes.decode("utf-8")
                    )
                else:
                    existing_ws_ont = GraphAOntology.model_validate(
                        yaml.safe_load(raw_ws_bytes.decode("utf-8"))
                    )
                pre_ws_canonical_sha = compute_ontology_sha256(existing_ws_ont)
            except (ValidationError, ValueError, TypeError, yaml.YAMLError) as exc:
                raise CommitValidationError(
                    f"Existing workspace ontology contract is invalid: {exc}",
                    error_code=ErrorCode.INVALID_WORKSPACE_ONTOLOGY,
                    status_code=422,
                    exit_code=4,
                ) from exc

            if exp_ws not in (pre_ws_canonical_sha, raw_ws_sha):
                raise ConcurrentModificationError(
                    f"Workspace OCC conflict: expected '{exp_ws}', but workspace contract "
                    f"canonical SHA-256 is '{pre_ws_canonical_sha}'."
                )

            if not allow_noop and pre_ws_canonical_sha == session.head_sha256:
                raise NoOpPromotionError(
                    "Workspace contract already matches session head ontology SHA-256."
                )

            if parse_semver_tuple(existing_ws_ont.ontology_version) > parse_semver_tuple(
                session.head_version
            ):
                if not acknowledge_historical_contract_warning:
                    raise HistoricalContractWarningUnacknowledgedError(
                        f"Workspace ontology version '{existing_ws_ont.ontology_version}' "
                        f"exceeds session head '{session.head_version}'; explicit warning "
                        "acknowledgement required."
                    )
                if (
                    not historical_contract_rationale
                    or len(historical_contract_rationale.strip()) < 10
                ):
                    raise HistoricalContractRationaleRequiredError(
                        "Historical contract rationale must be at least 10 characters."
                    )

            if enforce_lineage:
                session_version_shas = service._collect_session_lineage_shas(session_dir)
                if pre_ws_canonical_sha not in session_version_shas:
                    raise LineageRootMismatchError(
                        f"Workspace contract SHA-256 '{pre_ws_canonical_sha}' is not an "
                        f"ancestor in session '{session_id}' lineage."
                    )

            if parse_semver_tuple(existing_ws_ont.ontology_version) >= parse_semver_tuple(
                session.head_version
            ):
                if not acknowledge_historical_contract_warning:
                    raise HistoricalContractWarningUnacknowledgedError(
                        f"Workspace ontology version '{existing_ws_ont.ontology_version}' "
                        f"is >= session head '{session.head_version}'; explicit warning "
                        "acknowledgement required."
                    )
                if len(clean_hist_rationale) < 10:
                    raise HistoricalContractRationaleRequiredError(
                        "Historical contract rationale must be at least 10 characters."
                    )
        else:
            if exp_ws not in ("", "0" * 64):
                raise ConcurrentModificationError(
                    f"Workspace OCC conflict: expected '{exp_ws}', but no workspace contract "
                    f"exists at '{target_contract_path.name}'."
                )

        service._verify_promotion_governance(
            session_dir=session_dir,
            session=session,
            existing_ws_ont=existing_ws_ont,
            head_ont=head_ont,
            target_contract_path=target_contract_path,
            acknowledge_historical_contract_warning=acknowledge_historical_contract_warning,
            historical_contract_rationale=historical_contract_rationale,
        )

        version_tag = normalize_version_tag(session.head_version)
        now_iso = datetime.now(UTC).isoformat()

        # Phase 1: Write signed PROMOTION_INTENT to audit.jsonl
        intent_details: dict[str, Any] = {
            "workspace_path": str(target_contract_path),
            "pre_workspace_sha256": pre_ws_canonical_sha,
            "target_head_sha256": session.head_sha256,
            "target_version": version_tag,
            "rationale": rationale.strip(),
        }
        if clean_hist_rationale:
            intent_details["historical_contract_rationale"] = clean_hist_rationale

        intent_entry = service._append_raw_audit_entry_under_lock(
            session_dir=session_dir,
            session_id=session_id,
            action="PROMOTION_INTENT",
            target_version=version_tag,
            head_sha256=session.head_sha256,
            actor=actor,
            details=intent_details,
            prev_audit_sha=session.audit_chain_head_sha256,
        )
        session.audit_chain_head_sha256 = intent_entry["audit_sha256"]
        service._write_session_json(session_dir, session)

        # Phase 2: Stage canonical contract in workspace, fsync, atomic replace
        canonical_dict = canonicalize_graph_a_dict(head_ont)
        if target_contract_path.suffix.lower() == ".json":
            out_bytes = dumps_jcs_bytes(canonical_dict)
        else:
            out_bytes = yaml.safe_dump(canonical_dict, sort_keys=False).encode("utf-8")

        try:
            atomic_write_bytes(
                target_contract_path,
                out_bytes,
                tmp_name=f".graph_a.tmp.{secrets.token_hex(6)}",
                nofollow=True,
            )
            fsync_directory_best_effort(lock_dir)
        except BaseException as exc:
            abort_entry = service._append_raw_audit_entry_under_lock(
                session_dir=session_dir,
                session_id=session_id,
                action="PROMOTION_ABORTED",
                target_version=version_tag,
                head_sha256=session.head_sha256,
                actor=actor,
                details={
                    "workspace_path": str(target_contract_path),
                    "target_head_sha256": session.head_sha256,
                    "reason": str(exc),
                },
                prev_audit_sha=session.audit_chain_head_sha256,
            )
            session.audit_chain_head_sha256 = abort_entry["audit_sha256"]
            service._write_session_json(session_dir, session)
            raise

        # Record promotion.json and update SessionMetadata + PROMOTION_COMMITTED
        promo_record: dict[str, Any] = {
            "session_id": session_id,
            "promoted_version": version_tag,
            "head_sha256": session.head_sha256,
            "workspace_sha256": session.head_sha256,
            "pre_workspace_sha256": pre_ws_canonical_sha,
            "workspace_path": str(target_contract_path),
            "rationale": rationale.strip(),
            "promoted_by": actor,
            "promoted_at": now_iso,
        }
        if clean_hist_rationale:
            promo_record["historical_contract_rationale"] = clean_hist_rationale
        write_promotion_record(session_dir, promo_record)

        promoted_list = [normalize_version_tag(v) for v in session.promoted_versions]
        if version_tag not in promoted_list:
            promoted_list.append(version_tag)

        committed_details: dict[str, Any] = {
            "workspace_path": str(target_contract_path),
            "workspace_sha256": session.head_sha256,
            "pre_workspace_sha256": pre_ws_canonical_sha,
            "target_version": version_tag,
        }
        if clean_hist_rationale:
            committed_details["historical_contract_rationale"] = clean_hist_rationale

        committed_entry = service._append_raw_audit_entry_under_lock(
            session_dir=session_dir,
            session_id=session_id,
            action="PROMOTION_COMMITTED",
            target_version=version_tag,
            head_sha256=session.head_sha256,
            actor=actor,
            details=committed_details,
            prev_audit_sha=session.audit_chain_head_sha256,
        )
        final_audit_sha = committed_entry["audit_sha256"]

        updated_session = session.model_copy(
            update={
                "updated_at": now_iso,
                "promoted_versions": promoted_list,
                "last_promoted_sha256": session.head_sha256,
                "audit_chain_head_sha256": final_audit_sha,
            }
        )
        service._write_session_json(session_dir, updated_session)

        return PromotionResult(
            session_id=session_id,
            promoted_version=version_tag,
            head_sha256=session.head_sha256,
            workspace_sha256=session.head_sha256,
            pre_workspace_sha256=pre_ws_canonical_sha,
            target_contract_path=target_contract_path,
            promoted_at=now_iso,
            audit_head_sha256=final_audit_sha,
        )
