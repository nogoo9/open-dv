"""Headless Studio clarification and version loading helpers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import yaml

from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.models.graph_a import GraphAOntology
from opendv_service.studio.atomic_commit import ForbiddenAdminRequiredError
from opendv_service.studio.job_runners_evidence import (
    _STRICT_CORE_SEMVER_RE,
    load_active_session_evidence,
)
from opendv_service.studio.job_runners_privacy import _prepare_evidence_for_agent
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.sse_journal import SSEJournalWriter
from opendv_service.studio.workspace_security import WorkspaceSecurityManager


def run_clarify_session(
    session_manager: StudioSessionManager,
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
    """Generate polysemy clarification questions from active session evidence."""
    if actor_role == "viewer":
        raise ForbiddenAdminRequiredError(
            "Viewer role is not permitted to generate clarifications."
        )

    session_dir = session_manager.get_session_dir(session_id)
    chunks, tables = load_active_session_evidence(session_dir)
    if (mode or "").strip().lower() == "agent":
        sec_mgr = getattr(session_manager, "security_manager", None) or WorkspaceSecurityManager(
            studio_root=session_dir.parent
        )
        chunks, tables = _prepare_evidence_for_agent(chunks, tables, sec_mgr)

    if (not chunks and not tables and not candidate_terms) or (
        is_cancelled is not None and is_cancelled()
    ):
        return {"questions_count": 0, "question_ids": []}

    from opendv_service.construction.clarification_harness import ClarificationHarness

    harness = ClarificationHarness(session_dir=session_dir)
    questions = harness.generate_questions(
        candidate_terms=candidate_terms,
        evidence_chunks=chunks,
        tables=tables,
        max_questions=max_questions,
    )

    if writer is not None:
        for q in questions:
            writer.buffer_event(
                "clarification_generated",
                {
                    "job_id": job_id,
                    "question_id": q.question_id,
                    "focus_concept": q.focus_concept,
                },
            )
        writer.flush()

    return {
        "questions_count": len(questions),
        "question_ids": [q.question_id for q in questions],
    }


def _normalize_and_validate_version_dir(
    raw_version: str,
    session_dir: Path,
    session_id: str,
) -> tuple[str, Path]:
    """Validate strict `X.Y.Z` SemVer and assert containment within `session_dir/versions`."""
    cleaned = raw_version.strip() if isinstance(raw_version, str) else ""
    if cleaned.startswith("v") and not cleaned.startswith("vv"):
        cleaned = cleaned[1:]
    if not _STRICT_CORE_SEMVER_RE.fullmatch(cleaned):
        err = OpenDVError(
            f"Malformed or unknown version '{raw_version}' in session '{session_id}'.",
            error_code=ErrorCode.VERSION_NOT_FOUND,
        )
        err.status_code = err.http_status
        raise err

    versions_root = (session_dir / "versions").resolve()
    vdir = (session_dir / "versions" / f"v{cleaned}").resolve()
    if not vdir.is_relative_to(versions_root):
        err = OpenDVError(
            f"Version path traversal rejected for 'v{cleaned}' in session '{session_id}'.",
            error_code=ErrorCode.VERSION_NOT_FOUND,
        )
        err.status_code = err.http_status
        raise err
    return cleaned, vdir


def _load_session_version_ontology(
    session_dir: Path,
    session_id: str,
    active_version: str,
    head_version: str,
) -> GraphAOntology:
    """Load `versions/v{active_version}/ontology.{json,yaml}` or raise typed OpenDVError."""
    clean_active, vdir = _normalize_and_validate_version_dir(
        active_version, session_dir, session_id
    )
    ont_json = vdir / "ontology.json"
    ont_yaml = vdir / "ontology.yaml"
    if ont_json.is_file():
        return GraphAOntology.model_validate_json(ont_json.read_text(encoding="utf-8"))
    if ont_yaml.is_file():
        return GraphAOntology.model_validate(yaml.safe_load(ont_yaml.read_text(encoding="utf-8")))

    clean_head = head_version.strip()
    if clean_head.startswith("v") and not clean_head.startswith("vv"):
        clean_head = clean_head[1:]
    if clean_active != clean_head:
        err = OpenDVError(
            f"Requested base version 'v{clean_active}' does not exist in session '{session_id}'.",
            error_code=ErrorCode.VERSION_NOT_FOUND,
        )
        err.status_code = err.http_status
        raise err
    err = OpenDVError(
        f"Base ontology artifact missing for head version 'v{clean_active}' "
        f"in session '{session_id}'.",
        error_code=ErrorCode.CORRUPT_SESSION_RECORD,
    )
    err.status_code = err.http_status
    raise err
