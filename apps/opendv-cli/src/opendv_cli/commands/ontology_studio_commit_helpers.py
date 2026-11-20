"""Shared Ontology Studio CLI delta commit and SemVer computation helpers."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from opendv_cli.commands.ontology_studio_common import (
    _build_governance_validator,
    _build_signed_waiver_record,
    _err,
    _err_from_opendv_error,
)
from opendv_contracts.dtos.ontology import OntologyDelta, StewardWaiverRecord
from opendv_contracts.errors import OpenDVError
from opendv_core.models.graph_a import GraphAOntology
from opendv_service.ontology.applier import compute_next_semver
from opendv_service.ontology.validator import SemVerCalculusError
from opendv_service.studio.atomic_commit import (
    AtomicCommitService,
    ConcurrentModificationError,
    ForbiddenAdminRequiredError,
)
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.workspace_security import WorkspaceSecurityManager

if TYPE_CHECKING:
    from opendv_cli.executor import LocalExecutor


def compute_next_semver_or_err(
    head_version: str, temp_delta: OntologyDelta, base_ont: GraphAOntology
) -> str | tuple[int, Any]:
    """Compute next SemVer or return canonical ONT_EVO_CONTRACT_VIOLATION error tuple."""
    try:
        return compute_next_semver(head_version, temp_delta, base_ontology=base_ont)
    except (
        ValueError,
        TypeError,
        KeyError,
        SemVerCalculusError,
        ValidationError,
        AttributeError,
    ) as exc:
        return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"SemVer bump computation failure: {exc}")


def commit_validated_delta_cli(
    *,
    session_mgr: StudioSessionManager,
    sec_mgr: WorkspaceSecurityManager,
    executor: LocalExecutor | None,
    sid: str,
    delta_obj: OntologyDelta,
    principal: str,
    role_tier: str,
    if_match: str,
    waiver_record: StewardWaiverRecord | None,
    proposal_id: str | None = None,
) -> tuple[int, Any] | Any:
    """Shared CLI AtomicCommitService invocation for `delta apply` and `proposal accept`."""
    waivers_list = [waiver_record] if waiver_record is not None else []
    eval_dict = (
        {"waiver": waiver_record.model_dump(mode="json", exclude_none=True)}
        if waiver_record
        else None
    )
    try:
        gv = _build_governance_validator(executor, sec_mgr, verify_grounding=True)
        commit_svc = AtomicCommitService(
            session_manager=session_mgr, security_manager=sec_mgr, governance_validator=gv
        )
        return commit_svc.commit_delta(
            session_id=sid,
            delta=delta_obj,
            proposal_id=proposal_id,
            author=principal,
            actor_role=role_tier,
            if_match=if_match,
            waivers=waivers_list,
            governance_validator=gv,
            evaluation=eval_dict,
        )
    except ForbiddenAdminRequiredError as err_adm:
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", str(err_adm))
    except ConcurrentModificationError as err_occ:
        if proposal_id is not None and "already been resolved" in str(err_occ):
            return _err(6, "PROPOSAL_ALREADY_RESOLVED", str(err_occ))
        return _err(5, "ONT_EVO_CONCURRENT_MODIFICATION", str(err_occ))
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)
    except (RuntimeError, ValueError, TypeError, KeyError, ValidationError, OSError) as err_commit:
        return _err(4, "ONT_EVO_CONTRACT_VIOLATION", str(err_commit))


def commit_delta_with_waiver_cli(
    *,
    args: argparse.Namespace,
    session_mgr: StudioSessionManager,
    sec_mgr: WorkspaceSecurityManager,
    executor: LocalExecutor | None,
    sid: str,
    delta_obj: OntologyDelta,
    base_ontology: GraphAOntology,
    base_sha256: str,
    principal: str,
    role_tier: str,
    if_match: str,
    proposal_id: str | None = None,
) -> tuple[int, Any] | Any:
    """Build optional signed waiver record and commit validated delta."""
    waiver_res = _build_signed_waiver_record(
        args=args,
        delta_obj=delta_obj,
        base_ontology=base_ontology,
        base_sha256=base_sha256,
        proposal_id=proposal_id,
        principal=principal,
        role_tier=role_tier,
        executor=executor,
        sec_mgr=sec_mgr,
    )
    if isinstance(waiver_res, tuple):
        return waiver_res
    return commit_validated_delta_cli(
        session_mgr=session_mgr,
        sec_mgr=sec_mgr,
        executor=executor,
        sid=sid,
        delta_obj=delta_obj,
        principal=principal,
        role_tier=role_tier,
        if_match=if_match,
        waiver_record=waiver_res,
        proposal_id=proposal_id,
    )
