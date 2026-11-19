"""Live FIBO & Sources Q&A service using VaultAgentHarness (fail-closed if unconfigured)."""

from __future__ import annotations

import contextlib
import json
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from opendv_agent_harness.models import (
    AgentPersona,
    AgentTaskSpec,
    HarnessRunResult,
    ModelTier,
    PipelineStageId,
)
from opendv_agent_harness.protocol import VaultAgentHarness
from opendv_contracts.dtos.ontology import EvidenceRef
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_service.studio.agent_runner import StudioAgentRunner
from opendv_service.studio.agent_runner_helpers import _run_coro_sync, build_safe_agent_prompt
from opendv_service.studio.job_runners_evidence import load_active_session_evidence
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_sources.fibo.loader import get_default_fibo_loader


class FiboQARequest(BaseModel):
    """Input DTO for interactive Ask FIBO & Sources Q&A."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=1)
    source_ids: list[str] | None = None
    concept_uri: str | None = None


class FiboQAResponse(BaseModel):
    """Cited Q&A response with optional suggested ontology delta actions."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    citations: list[EvidenceRef] = Field(default_factory=list)
    suggested_actions: list[dict[str, Any]] = Field(default_factory=list)


def answer_fibo_qa(
    *,
    session_mgr: StudioSessionManager,
    session_id: str,
    request: FiboQARequest,
    harness: VaultAgentHarness | None = None,
    state_container: Any | None = None,
) -> FiboQAResponse:
    """Answer FIBO/domain evidence questions via active harness; fail closed if unconfigured."""
    active_harness = harness
    if isinstance(active_harness, StudioAgentRunner):
        active_harness = active_harness._resolve_runtime_harness()
    if active_harness is None:
        runner = StudioAgentRunner(state_container=state_container)
        active_harness = runner._resolve_runtime_harness()

    session_dir = session_mgr.get_session_dir(session_id)
    chunks, tables = load_active_session_evidence(session_dir)
    if request.source_ids:
        allowed = set(request.source_ids)
        chunks = [
            c
            for c in chunks
            if c.document_id in allowed or (c.metadata or {}).get("source_id") in allowed
        ]
        tables = [t for t in tables if (t.metadata or {}).get("source_id") in allowed]

    fibo_hits: list[dict[str, Any]] = []
    with contextlib.suppress(Exception):
        loader = get_default_fibo_loader()
        fibo_hits = loader.search_candidates(request.question, limit=5)

    evidence_context = build_safe_agent_prompt(
        base_ontology=None,
        evidence_chunks=chunks[:20],
        tables=tables[:10],
        clarification_answers={},
        fibo_modules=[],
        steering_prompt=None,
    )
    prompt = (
        f"Question: {request.question}\n"
        f"Concept URI focus: {request.concept_uri or 'none'}\n"
        f"FIBO candidate context: {json.dumps(fibo_hits)}\n"
        f"Session safe evidence:\n{evidence_context}"
    )

    spec = AgentTaskSpec(
        task_id=f"qa-{uuid.uuid4().hex[:8]}",
        run_id=f"qa-run-{uuid.uuid4().hex[:8]}",
        trace_id=uuid.uuid4().hex,
        session_id=session_id,
        stage_id=PipelineStageId.STAGE_02_SCOUT_UNSTRUCTURED,
        persona=AgentPersona.DOMAIN_CLARIFIER,
        model_tier=ModelTier.HIGH_REASONING,
        system_instructions=(
            "Answer domain ontology and FIBO mapping questions grounded in retrieved "
            "FIBO concepts and safe session evidence. Cite EvidenceRef anchors."
        ),
        prompt=prompt,
        output_schema=FiboQAResponse,
    )
    res: HarnessRunResult[Any] = _run_coro_sync(active_harness.run_task(spec), timeout=20.0)
    if isinstance(res.structured_output, FiboQAResponse):
        return res.structured_output
    if isinstance(res.structured_output, dict):
        return FiboQAResponse.model_validate(res.structured_output)
    if res.text_output:
        with contextlib.suppress(Exception):
            return FiboQAResponse.model_validate_json(res.text_output)
    raise OpenDVError(
        "FIBO Q&A harness did not produce structured FiboQAResponse output.",
        error_code=ErrorCode.AGENT_UNAVAILABLE,
    )


__all__ = [
    "FiboQARequest",
    "FiboQAResponse",
    "answer_fibo_qa",
]
