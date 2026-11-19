"""Headless Studio clarify and construct job runners plus `StudioJobRunners` facade."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from opendv_contracts.dtos.ontology import (
    PATH_A_GROUNDING_THRESHOLD,
    OntologyDelta,
    OntologyProposal,
)
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.ontology.applier import compute_next_semver
from opendv_service.studio.atomic_commit import (
    AtomicCommitService,
    ConcurrentModificationError,
    ForbiddenAdminRequiredError,
    parse_strong_etag,
)
from opendv_service.studio.job_runners_clarify import (
    _load_session_version_ontology,
    _normalize_and_validate_version_dir,
    run_clarify_session,
)
from opendv_service.studio.job_runners_evidence import (
    _VALID_CONSTRUCT_MODES,
    AgentProposalAcceptForbiddenError,
    _atomic_write_bytes,
    _prepare_evidence_for_agent,
    load_active_session_evidence,
    load_binding_clarification_answers,
)
from opendv_service.studio.job_runners_parse import run_parse_session_sources
from opendv_service.studio.locks import reentrant_session_lock
from opendv_service.studio.session_manager import (
    StudioSessionManager,
    append_signed_audit_record,
)
from opendv_service.studio.sse_journal import SSEJournalWriter
from opendv_service.studio.workspace_security import WorkspaceSecurityManager
from opendv_sources.ingestion.service import IngestionService

if TYPE_CHECKING:
    from opendv_service.construction.deterministic_pipeline import (
        DeterministicConstructionPipeline,
    )


def _invoke_agent_runner(
    runner: Callable[..., OntologyProposal],
    base_kwargs: dict[str, Any],
    optional_kwargs: dict[str, Any],
) -> OntologyProposal:
    """Invoke `runner` forwarding optional telemetry args when accepted by its signature."""
    call_kwargs = dict(base_kwargs)
    try:
        sig = inspect.signature(runner)
        has_var_kw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        for key, val in optional_kwargs.items():
            if has_var_kw or key in sig.parameters:
                call_kwargs[key] = val
    except (ValueError, TypeError):
        pass
    return runner(**call_kwargs)


def run_construct_session(
    session_manager: StudioSessionManager,
    session_id: str,
    *,
    base_version: str | None = None,
    base_sha256: str | None = None,
    job_id: str = "construct",
    auto_commit: bool = False,
    fibo_modules: Sequence[str] | None = None,
    mode: str = "deterministic",
    if_match: str | None = None,
    steering_prompt: str | None = None,
    actor: str = "human:local_admin",
    actor_role: str = "admin",
    min_confidence: float = PATH_A_GROUNDING_THRESHOLD,
    require_admin_for_auto_commit: bool = False,
    writer: SSEJournalWriter | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    on_stage: Callable[[str], None] | None = None,
    pipeline: DeterministicConstructionPipeline | None = None,
    agent_runner: Callable[..., OntologyProposal] | None = None,
    security_manager: WorkspaceSecurityManager | None = None,
) -> dict[str, Any]:
    """Execute ontology construction producing a persisted proposal or governed version commit."""
    if actor_role == "viewer":
        raise ForbiddenAdminRequiredError("Viewer role is not permitted to construct proposals.")

    clean_mode = (mode or "deterministic").strip().lower()
    if clean_mode not in _VALID_CONSTRUCT_MODES:
        allowed_modes = sorted(_VALID_CONSTRUCT_MODES)
        raise OpenDVError(
            f"Unsupported construction mode '{mode}'; expected one of {allowed_modes}.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    if auto_commit:
        if actor.startswith("agent:") or actor_role == "automated":
            raise AgentProposalAcceptForbiddenError(
                "Agent and automated principals are forbidden from auto-commit construction."
            )
        if clean_mode == "agent":
            raise OpenDVError(
                "Auto-commit construction is only permitted in deterministic mode; "
                "agent-generated proposals require human review.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if require_admin_for_auto_commit and actor_role != "admin":
            raise ForbiddenAdminRequiredError(
                "Administrator role is required for auto-commit construction."
            )

    session_dir = session_manager.get_session_dir(session_id)
    session_meta = session_manager.get_session(session_id)
    raw_target_version = session_meta.head_version if base_version is None else base_version
    active_version, _ = _normalize_and_validate_version_dir(
        raw_target_version, session_dir, session_id
    )
    sec_mgr = (
        security_manager
        or getattr(session_manager, "security_manager", None)
        or WorkspaceSecurityManager(studio_root=session_dir.parent)
    )

    base_ontology = _load_session_version_ontology(
        session_dir=session_dir,
        session_id=session_id,
        active_version=active_version,
        head_version=session_meta.head_version,
    )
    canonical_base_sha = compute_ontology_sha256(base_ontology)

    clean_base_sha = parse_strong_etag(base_sha256) if base_sha256 else None
    if clean_base_sha is not None and clean_base_sha != canonical_base_sha:
        raise ConcurrentModificationError(
            f"base_sha256 '{clean_base_sha}' does not match loaded base ontology "
            f"'v{active_version}' digest '{canonical_base_sha}'."
        )

    clean_if_match = parse_strong_etag(if_match) if if_match else None
    if clean_if_match is not None and clean_if_match != canonical_base_sha:
        raise ConcurrentModificationError(
            f"If-Match precondition '{clean_if_match}' does not match loaded base ontology "
            f"'v{active_version}' digest '{canonical_base_sha}'."
        )

    clean_auto_precond: str | None = None
    if auto_commit:
        clean_auto_precond = clean_if_match or clean_base_sha
        if not clean_auto_precond:
            raise OpenDVError(
                "Auto-commit construction requires an explicit If-Match header or base_sha256 "
                "precondition anchoring the expected head ontology SHA-256.",
                error_code=ErrorCode.MISSING_AUTO_COMMIT_PRECONDITION,
            )
        if canonical_base_sha != session_meta.head_sha256:
            raise ConcurrentModificationError(
                f"Auto-commit requires constructing against active head "
                f"('{session_meta.head_sha256}'), got base digest '{canonical_base_sha}'."
            )
        if clean_auto_precond != session_meta.head_sha256:
            raise ConcurrentModificationError(
                f"Auto-commit precondition '{clean_auto_precond}' does not match active session "
                f"head digest '{session_meta.head_sha256}'."
            )

    chunks, tables = load_active_session_evidence(session_dir)
    answers_dict = load_binding_clarification_answers(session_dir)

    used_agent_runner = clean_mode == "agent" or (
        clean_mode == "auto" and not auto_commit and agent_runner is not None
    )
    resolved_engine_mode = "agent" if used_agent_runner else "deterministic"
    if used_agent_runner:
        active_runner = agent_runner
        if active_runner is None:
            from opendv_service.studio.agent_runner import StudioAgentRunner

            active_runner = StudioAgentRunner()
        agent_chunks, agent_tables = _prepare_evidence_for_agent(chunks, tables, sec_mgr)
        proposal = _invoke_agent_runner(
            active_runner,
            {
                "base_ontology": base_ontology,
                "evidence_chunks": agent_chunks,
                "tables": agent_tables,
                "clarification_answers": answers_dict,
                "session_dir": session_dir,
                "fibo_modules": list(fibo_modules or []),
                "steering_prompt": steering_prompt,
                "job_id": job_id,
                "principal": actor,
                "mode": clean_mode,
            },
            {
                "writer": writer,
                "session_id": session_id,
                "on_stage": on_stage,
            },
        )
    else:
        from opendv_service.construction.deterministic_pipeline import (
            DeterministicConstructionPipeline,
        )
        from opendv_service.construction.models import ConstructOptions

        if writer is not None:
            writer.buffer_event(
                "stage_update",
                {
                    "job_id": job_id,
                    "stage": "construct",
                    "mode": "deterministic",
                    "message": "Running ontology construction in mode=deterministic",
                },
            )
        det_pipeline = pipeline or DeterministicConstructionPipeline()
        opts = ConstructOptions(
            mode="deterministic",
            auto_commit=False,
            session_id=session_id,
            session_dir=session_dir,
            min_confidence=min_confidence,
            actor=actor,
            principal=actor,
            if_match=None,
            steering_prompt=steering_prompt,
        )
        proposal = det_pipeline.construct_proposal(
            base_ontology=base_ontology,
            evidence_chunks=chunks,
            tables=tables,
            clarification_answers=answers_dict,
            options=opts,
        )

    proposal_ops = list(proposal.delta.operations)
    raw_delta_dict = proposal.delta.model_dump(mode="json")
    for alias_key in ("base_sha256", "base_version", "target_version"):
        raw_delta_dict.pop(alias_key, None)
    raw_delta_dict["parent_sha256"] = canonical_base_sha
    raw_delta_dict["base_ontology_version"] = active_version
    if proposal_ops:
        probe_delta = proposal.delta.model_copy(update={"target_ontology_version": None})
        probe_delta.__pydantic_fields_set__.discard("target_ontology_version")
        expected_semver = compute_next_semver(active_version, probe_delta, base_ontology)
        raw_delta_dict["target_ontology_version"] = expected_semver
    else:
        raw_delta_dict.pop("target_ontology_version", None)

    delta = OntologyDelta.model_validate(raw_delta_dict)
    assert delta.base_sha256 == canonical_base_sha
    proposal = proposal.model_copy(
        update={
            "delta": delta,
            "base_ontology_sha256": canonical_base_sha,
        }
    )

    if writer is not None:
        for op in proposal_ops:
            ext_mappings = getattr(op, "external_mappings", None)
            if ext_mappings:
                for m in ext_mappings:
                    writer.buffer_event(
                        "fibo_grounding",
                        {
                            "job_id": job_id,
                            "concept_uri": getattr(op, "concept_uri", ""),
                            "matched_fibo_uri": m.target_uri,
                            "score": m.confidence,
                        },
                    )
        writer.flush()

    if is_cancelled is not None and is_cancelled():
        return {
            "proposal_id": proposal.proposal_id,
            "status": "cancelled",
            "mode": resolved_engine_mode,
            "message": "Job cancelled before commit.",
        }

    proposals_dir = session_dir / "proposals"
    prop_file = proposals_dir / f"{proposal.proposal_id}.json"
    lock_file = session_dir / ".session.lock"
    delta_json = delta.model_dump(mode="json", exclude_none=True)
    staged_candidates = list(
        getattr(proposal, "staged_candidates", None)
        or (delta.metadata or {}).get("staged_candidates", [])
    )
    created_at_str = proposal.created_at_utc or datetime.now(UTC).isoformat()
    clean_job_id = (job_id or "").strip()
    if clean_job_id:
        proposer_identity = f"{resolved_engine_mode}:{clean_job_id}"
    elif actor.startswith(("system:", "agent:", "deterministic:")):
        proposer_identity = actor
    else:
        proposer_identity = f"system:{resolved_engine_mode}"

    proposal_record: dict[str, Any] = {
        **proposal.model_dump(mode="json", exclude_none=True),
        "proposal_id": proposal.proposal_id,
        "session_id": session_id,
        "status": "PENDING",
        "mode": resolved_engine_mode,
        "base_version": active_version,
        "base_sha256": canonical_base_sha,
        "proposer": proposer_identity,
        "proposer_actor": resolved_engine_mode,
        "triggered_by": actor,
        "created_at": created_at_str,
        "operations": list(delta_json.get("operations", [])),
        "staged_candidates": staged_candidates,
        "rationale": proposal.rationale,
        "confidence_score": proposal.confidence_score,
        "diagnostics": list(proposal.diagnostics or []),
        "delta": delta_json,
    }
    with reentrant_session_lock(lock_file, session_id=session_id):
        prop_payload = json.dumps(proposal_record, indent=2).encode("utf-8")
        _atomic_write_bytes(prop_file, prop_payload)

    if not auto_commit:
        return {
            "proposal_id": proposal.proposal_id,
            "operations_count": len(proposal_ops),
            "mode": resolved_engine_mode,
        }

    assert clean_auto_precond is not None
    if is_cancelled is not None and is_cancelled():
        return {
            "proposal_id": proposal.proposal_id,
            "status": "cancelled",
            "mode": resolved_engine_mode,
            "message": "Job cancelled before commit.",
        }

    if on_stage is not None:
        on_stage("committing")

    if len(proposal_ops) == 0:
        with reentrant_session_lock(lock_file, session_id=session_id):
            fresh_meta = session_manager.get_session(session_id)
            if fresh_meta.head_sha256 != clean_auto_precond:
                raise ConcurrentModificationError(
                    f"Session head moved concurrently during construct: expected "
                    f"'{clean_auto_precond}', got '{fresh_meta.head_sha256}'."
                )
            audit_rec = append_signed_audit_record(
                session_dir=session_dir,
                action="NOOP_CONSTRUCT",
                actor=actor,
                details={
                    "job_id": job_id,
                    "proposal_id": proposal.proposal_id,
                    "base_version": f"v{active_version}",
                    "base_sha256": canonical_base_sha,
                    "mode": resolved_engine_mode,
                },
                security_manager=sec_mgr,
            )
            audit_sha = (
                str(audit_rec["audit_sha256"]) if isinstance(audit_rec, dict) else str(audit_rec)
            )
        if writer is not None:
            writer.buffer_event(
                "noop_construct",
                {
                    "job_id": job_id,
                    "proposal_id": proposal.proposal_id,
                    "head_version": fresh_meta.head_version,
                    "head_sha256": fresh_meta.head_sha256,
                    "mode": resolved_engine_mode,
                },
            )
            writer.flush()
        return {
            "proposal_id": proposal.proposal_id,
            "status": "NOOP_CONSTRUCT",
            "auto_committed": False,
            "operations_count": 0,
            "target_version": fresh_meta.head_version,
            "target_sha256": fresh_meta.head_sha256,
            "audit_head_sha256": audit_sha,
            "mode": resolved_engine_mode,
        }

    atomic_svc = AtomicCommitService(
        session_manager=session_manager,
        security_manager=sec_mgr,
    )
    commit_res = atomic_svc.commit_delta(
        session_id=session_id,
        delta=delta,
        proposal_id=proposal.proposal_id,
        author=actor,
        if_match=f'"{clean_auto_precond}"',
        actor_role=actor_role,
        waivers=[],
    )

    if writer is not None:
        writer.buffer_event(
            "version_committed",
            {
                "job_id": job_id,
                "target_version": commit_res.version,
                "target_sha256": commit_res.target_sha256,
                "mode": resolved_engine_mode,
            },
        )
        writer.flush()

    return {
        "proposal_id": proposal.proposal_id,
        "auto_committed": True,
        "operations_count": len(proposal_ops),
        "target_version": commit_res.version,
        "target_sha256": commit_res.target_sha256,
        "audit_head_sha256": commit_res.audit_head_sha256,
        "mode": resolved_engine_mode,
    }


class StudioJobRunners:
    """Object-oriented facade bundling headless Studio parse, clarify, and construct runners."""

    def __init__(
        self,
        session_manager: StudioSessionManager,
        security_manager: WorkspaceSecurityManager | None = None,
        ingestion_service: IngestionService | None = None,
        pipeline: DeterministicConstructionPipeline | None = None,
        agent_runner: Callable[..., OntologyProposal] | None = None,
    ) -> None:
        self.session_manager = session_manager
        self.security_manager = security_manager
        self.ingestion_service = ingestion_service
        self.pipeline = pipeline
        self.agent_runner = agent_runner

    def run_parse(
        self,
        session_id: str,
        *,
        job_id: str = "parse",
        source_ids: Sequence[str] | None = None,
        ocr_mode: str = "auto",
        actor_role: str = "admin",
        writer: SSEJournalWriter | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        on_progress: Callable[[str, int], None] | None = None,
        fail_fast_on_source_error: bool = False,
    ) -> dict[str, Any]:
        """Delegate to `run_parse_session_sources`."""
        svc = (
            self.ingestion_service
            if self.ingestion_service is not None and ocr_mode == "auto"
            else None
        )
        return run_parse_session_sources(
            session_manager=self.session_manager,
            session_id=session_id,
            job_id=job_id,
            source_ids=source_ids,
            ocr_mode=ocr_mode,
            actor_role=actor_role,
            writer=writer,
            is_cancelled=is_cancelled,
            on_progress=on_progress,
            ingestion_service=svc,
            fail_fast_on_source_error=fail_fast_on_source_error,
        )

    def run_clarify(
        self,
        session_id: str,
        *,
        job_id: str = "clarify",
        mode: str = "auto",
        max_questions: int = 5,
        candidate_terms: Sequence[str | dict[str, Any]] | None = None,
        actor_role: str = "admin",
        writer: SSEJournalWriter | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Delegate to `run_clarify_session`."""
        return run_clarify_session(
            session_manager=self.session_manager,
            session_id=session_id,
            job_id=job_id,
            mode=mode,
            max_questions=max_questions,
            candidate_terms=candidate_terms,
            actor_role=actor_role,
            writer=writer,
            is_cancelled=is_cancelled,
        )

    def run_construct(
        self,
        session_id: str,
        *,
        base_version: str | None = None,
        base_sha256: str | None = None,
        job_id: str = "construct",
        auto_commit: bool = False,
        fibo_modules: Sequence[str] | None = None,
        mode: str = "deterministic",
        if_match: str | None = None,
        actor: str = "human:local_admin",
        actor_role: str = "admin",
        min_confidence: float = PATH_A_GROUNDING_THRESHOLD,
        require_admin_for_auto_commit: bool = False,
        writer: SSEJournalWriter | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        on_stage: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Delegate to `run_construct_session`."""
        return run_construct_session(
            session_manager=self.session_manager,
            session_id=session_id,
            base_version=base_version,
            base_sha256=base_sha256,
            job_id=job_id,
            auto_commit=auto_commit,
            fibo_modules=fibo_modules,
            mode=mode,
            if_match=if_match,
            actor=actor,
            actor_role=actor_role,
            min_confidence=min_confidence,
            require_admin_for_auto_commit=require_admin_for_auto_commit,
            writer=writer,
            is_cancelled=is_cancelled,
            on_stage=on_stage,
            pipeline=self.pipeline,
            agent_runner=self.agent_runner,
            security_manager=self.security_manager,
        )
