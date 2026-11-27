"""Coordinator (6 tools) implementations, HarnessBudget tracker, and hold-drain coordinator."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from opendv.grounding.spi import KnowledgeSource

from opendv.agents.ports import ScoutToolDispatcher
from opendv.models.diagnostics import ClarificationQuestion
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import RunCheckpointRecord
from opendv.models.ontology import GraphAOntology
from opendv.models.ontology_deltas import (
    AGENT_ADDITIVE_OPS,
    HUMAN_ONLY_OPS,
    SERVER_ONLY_OPS,
)
from opendv.models.project import (
    ConceptMemorySpec,
    DiscoveryMode,
    EvolutionLockSpec,
    RevisionMetadata,
    StagedCandidateSpec,
)
from opendv.models.wire_dtos_discovery import (
    ClientAdditiveOp,
    ConceptSummary,
    DeltaStageResp,
    FiboQueryReq,
    FiboQueryResp,
    HoldAck,
    ScoutDigest,
)
from opendv.service._mcp_ports import (
    Bm25MatcherPort,
    BrokerBudgetTracker,
    BrokerHoldCoordinator,
    ExternalMcpGatewayPort,
    OcrProviderFn,
    ProjectStatePort,
)
from opendv.service._mcp_scout_tools import (
    scout_call_external_mcp,
    scout_inspect_table_cluster,
    scout_inspect_table_profile,
    scout_read_document_pages,
    scout_view_page_image,
)
from opendv.service.workflows._ontology_grounding import GROUNDING_MIN_CONFIDENCE

_REDACTED_LEAK_RE = re.compile(r"(?:<MASKED_PII>|\[REDACTED_)")


@dataclass
class BrokerToolContext:
    """Injected dependencies and state for a Step 4 JobMcpBroker session."""

    project_id: str
    run_id: str
    state_store: ProjectStatePort
    bm25_matcher: Bm25MatcherPort | None = None
    external_gateway: ExternalMcpGatewayPort | None = None
    scout_dispatcher: ScoutToolDispatcher | None = None
    budget_tracker: BrokerBudgetTracker = field(default_factory=BrokerBudgetTracker)
    hold_coord: BrokerHoldCoordinator | None = None
    workspace_seeds: list[ConceptSummary] = field(default_factory=list)
    parsed_pages: dict[str, list[str]] = field(default_factory=dict)
    source_path_resolver: Callable[[str, str], Path | None] | None = None
    ocr_provider: OcrProviderFn | None = None
    ontology_rev_id: str | None = None
    sources_fingerprint: str = "fp-step4"
    discovery_mode: DiscoveryMode = "incremental_new"
    fibo_source: KnowledgeSource | None = None
    steward_answers: Mapping[str, str] | Sequence[str] | None = None

    def coord(self) -> BrokerHoldCoordinator:
        if self.hold_coord is None:
            self.hold_coord = BrokerHoldCoordinator(
                project_id=self.project_id,
                run_id=self.run_id,
                state_store=self.state_store,
            )
        return self.hold_coord


def _latest_rev_payload(
    state: ProjectStatePort, project_id: str, rev_kind: str
) -> dict[str, Any] | None:
    revs = state.list_revisions(project_id, rev_kind=rev_kind)
    if not revs:
        return None
    raw = revs[-1].get("payload", revs[-1].get("payload_json"))
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        loaded = json.loads(raw)
        return dict(loaded) if isinstance(loaded, dict) else None
    if hasattr(raw, "model_dump") and raw is not None:
        dumped = raw.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, dict) else None
    return None


def _norm_slug(name: str) -> str:
    low = name.strip().lower()
    for prefix in ("hub_", "lnk_", "t_lnk_", "sal_", "hal_", "sat_", "mas_", "esat_", "ref_"):
        if low.startswith(prefix):
            low = low[len(prefix) :]
            break
    if low.endswith("_canonical"):
        low = low[: -len("_canonical")]
    return re.sub(r"[^a-z0-9]+", "", low)


def _locked_hub_names(state: ProjectStatePort, project_id: str) -> set[str]:
    raw_lock = _latest_rev_payload(state, project_id, "evolution_lock")
    if not raw_lock:
        return set()
    locks = EvolutionLockSpec.model_validate(raw_lock)
    return {_norm_slug(name) for name in locks.locked_hub_bks}


def _locked_link_names(state: ProjectStatePort, project_id: str) -> set[str]:
    raw_lock = _latest_rev_payload(state, project_id, "evolution_lock")
    if not raw_lock:
        return set()
    locks = EvolutionLockSpec.model_validate(raw_lock)
    return set(locks.locked_links.keys())


def _slug_key(prefix: str, name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "_", name.lower()).strip("_")[:28] or "concept"
    sha8 = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{slug}-{sha8}"


def coord_search_concepts(
    ctx: BrokerToolContext, query: str, include_seeds: bool = True
) -> list[ConceptSummary]:
    """Search committed Graph A, staged in-run candidates, concept memory, and workspace seeds."""
    ctx.budget_tracker.check_and_consume({"query": query}, tool_name="search_concepts")
    ctx.coord().emit("TOOL_CALL", {"tool_name": "search_concepts", "args_redacted": query})
    q = query.strip().lower()
    locked = _locked_hub_names(ctx.state_store, ctx.project_id)
    results: list[ConceptSummary] = []

    raw_ont = _latest_rev_payload(ctx.state_store, ctx.project_id, "ontology_version")
    if raw_ont:
        ont = GraphAOntology.model_validate(raw_ont)
        for c in ont.concepts:
            if not q or q in c.concept_name.lower() or any(q in s.lower() for s in c.synonyms):
                results.append(
                    ConceptSummary(
                        concept_uri=c.concept_uri,
                        concept_name=c.concept_name,
                        source_layer="committed_graph_a",
                        locked_in_vault=_norm_slug(c.concept_name) in locked,
                    )
                )

    for rev in ctx.state_store.list_revisions(ctx.project_id, rev_kind="staged_candidate"):
        raw_p = rev.get("payload", rev.get("payload_json"))
        if raw_p is None:
            continue
        cand = (
            raw_p
            if isinstance(raw_p, StagedCandidateSpec)
            else StagedCandidateSpec.model_validate(raw_p)
        )
        if not q or q in cand.concept_name.lower():
            results.append(
                ConceptSummary(
                    concept_uri=cand.concept_uri,
                    concept_name=cand.concept_name,
                    source_layer="in_run",
                    locked_in_vault=False,
                )
            )

    for rev in ctx.state_store.list_revisions(ctx.project_id, rev_kind="concept_memory"):
        raw_p = rev.get("payload", rev.get("payload_json"))
        if raw_p is None:
            continue
        mem = (
            raw_p
            if isinstance(raw_p, ConceptMemorySpec)
            else ConceptMemorySpec.model_validate(raw_p)
        )
        if (not q or q in mem.term.lower()) and mem.concept_uri:
            results.append(
                ConceptSummary(
                    concept_uri=mem.concept_uri,
                    concept_name=mem.term,
                    source_layer="concept_memory",
                    locked_in_vault=False,
                )
            )

    if include_seeds:
        for seed in ctx.workspace_seeds:
            if not q or q in seed.concept_name.lower():
                results.append(seed)

    dumped = [r.model_dump(mode="json") for r in results]
    ctx.budget_tracker.check_payload_size(dumped, label="search_concepts result")
    ctx.coord().emit(
        "TOOL_RESULT",
        {"tool_name": "search_concepts", "ok": True, "summary": f"{len(results)} hits"},
    )
    return results


def coord_search_fibo(
    ctx: BrokerToolContext,
    query: str,
    domain_namespace: str | None = None,
    top_k: int = 10,
) -> FiboQueryResp:
    """Query precompiled FIBO BM25 matcher via Bm25MatcherPort."""
    ctx.budget_tracker.check_and_consume({"query": query, "top_k": top_k}, tool_name="search_fibo")
    ctx.coord().emit("TOOL_CALL", {"tool_name": "search_fibo", "args_redacted": query})
    if ctx.bm25_matcher is None:
        resp = FiboQueryResp(matches=[])
    else:
        resp = ctx.bm25_matcher.query(
            FiboQueryReq(query=query, domain_namespace=domain_namespace, top_k=top_k)
        )
    ctx.budget_tracker.check_payload_size(resp.model_dump(mode="json"), label="search_fibo result")
    ctx.coord().emit(
        "TOOL_RESULT",
        {"tool_name": "search_fibo", "ok": True, "summary": f"{len(resp.matches)} matches"},
    )
    return resp


def _check_quote_no_leaks(text: str, *, context: str) -> None:
    if _REDACTED_LEAK_RE.search(text):
        raise OpenDVError(
            f"Redacted placeholder leaked in {context}.",
            error_code=ErrorCode.REDACTED_PLACEHOLDER_LEAK,
        )


def _derive_grounding_path(op: Any) -> Literal["PATH_A1", "PATH_A2", "PATH_B1", "PATH_B2"]:
    mappings = list(getattr(op, "external_mappings", ()) or ())
    single = getattr(op, "mapping", None)
    if single is not None:
        mappings.append(single)
    qualifying = [m for m in mappings if m.confidence >= GROUNDING_MIN_CONFIDENCE]
    if any(m.mapping_type == "skos:exactMatch" for m in qualifying):
        return "PATH_A1"
    if any(m.mapping_type == "skos:closeMatch" for m in qualifying):
        return "PATH_A2"
    if (
        getattr(op, "table_bindings", None)
        or getattr(op, "source_citations", None)
        or getattr(op, "mcp_evidence", None)
        or getattr(op, "op_type", "") in ("AddTableBinding", "AddSourceCitation", "AddMcpEvidence")
    ):
        return "PATH_B1"
    return "PATH_B2"


def coord_propose_ontology_delta(
    ctx: BrokerToolContext, ops: Sequence[ClientAdditiveOp]
) -> DeltaStageResp:
    """Validate and stage additive _AOps into revisions (rejecting _DOps/server ops/locks/leaks)."""
    if ctx.coord().hold_requested:
        raise OpenDVError(
            "Cannot propose ontology deltas after HOLD_REQUESTED.",
            error_code=ErrorCode.HOLD_CHECKPOINTED,
            run_id=ctx.run_id,
        )
    raw_ops = [op.model_dump(mode="json") for op in ops]
    ctx.budget_tracker.check_and_consume(raw_ops, tool_name="propose_ontology_delta")
    ctx.coord().emit(
        "TOOL_CALL",
        {"tool_name": "propose_ontology_delta", "args_redacted": f"{len(ops)} ops"},
    )

    locked = _locked_hub_names(ctx.state_store, ctx.project_id)
    locked_links = _locked_link_names(ctx.state_store, ctx.project_id)
    raw_ont = _latest_rev_payload(ctx.state_store, ctx.project_id, "ontology_version")
    uri_to_name: dict[str, str] = {}
    if raw_ont:
        ont = GraphAOntology.model_validate(raw_ont)
        uri_to_name = {c.concept_uri: c.concept_name for c in ont.concepts}

    ctx.state_store.get_project(ctx.project_id)
    proj_tag = hashlib.sha256(ctx.project_id.encode("utf-8")).hexdigest()[:12]
    staged_records: list[tuple[str, str, StagedCandidateSpec, RevisionMetadata]] = []
    for item in ops:
        op = item.op
        op_type = op.op_type
        if op_type in SERVER_ONLY_OPS:
            raise OpenDVError(
                f"Server-only delta op '{op_type}' is forbidden in coordinator MCP calls.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                run_id=ctx.run_id,
            )
        if op_type in HUMAN_ONLY_OPS or op_type not in AGENT_ADDITIVE_OPS:
            raise OpenDVError(
                f"Human-only/destructive delta op '{op_type}' is forbidden for agents.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
                run_id=ctx.run_id,
            )

        if op.op_type == "AddSourceCitation":
            _check_quote_no_leaks(op.citation.verbatim_quote, context="AddSourceCitation")
        elif op.op_type == "AddMcpEvidence":
            _check_quote_no_leaks(op.evidence.masked_excerpt, context="AddMcpEvidence")
        elif op.op_type == "AddConcept":
            for cit in op.source_citations:
                _check_quote_no_leaks(cit.verbatim_quote, context="AddConcept.source_citations")
            for ev in op.mcp_evidence:
                _check_quote_no_leaks(ev.masked_excerpt, context="AddConcept.mcp_evidence")

        if (
            op.op_type == "AddRelationship"
            and (
                op.relationship.relationship_uri in locked_links
                or op.relationship.target_link in locked_links
            )
        ):
            raise OpenDVError(
                f"Cannot propose AddRelationship onto locked link '{op.relationship.target_link}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
                run_id=ctx.run_id,
            )

        target_uri = getattr(op, "concept_uri", None)
        target_name = uri_to_name.get(target_uri, "") if target_uri else ""
        is_locked = bool(target_name and _norm_slug(target_name) in locked)
        if is_locked and (
            op.op_type in ("SetConceptKeyAuthority", "AddKeyAuthorityPattern")
            or (op.op_type == "AddAttribute" and op.attribute.is_business_key)
            or (op.op_type == "UpdateAttributeMetadata" and op.is_business_key is not None)
        ):
            raise OpenDVError(
                f"Cannot modify locked business key on '{target_name}' via '{op_type}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
                run_id=ctx.run_id,
            )

        if op.op_type == "AddConcept":
            c_name = op.concept_name
            c_uri = op.concept_uri
            cand_id = _slug_key("CAND", c_name)
            spec = StagedCandidateSpec(
                candidate_id=cand_id,
                concept_name=c_name,
                concept_uri=c_uri,
                grounding_path=_derive_grounding_path(op),
                table_bindings=list(op.table_bindings),
                source_citations=list(op.source_citations),
                mcp_evidence=list(op.mcp_evidence),
                status="QUARANTINED",
            )
        else:
            raw_ref = getattr(op, "concept_uri", "") or getattr(op, "relationship", None) or op_type
            c_uri = str(raw_ref)
            op_attr = (
                getattr(op, "attribute_name", None)
                or getattr(getattr(op, "attribute", None), "name", None)
                or ""
            )
            cand_id = _slug_key("CAND", f"{op_type}-{c_uri}-{op_attr}-{op.model_dump_json()}")
            spec = StagedCandidateSpec(
                candidate_id=cand_id,
                concept_name=target_name or op_type,
                concept_uri=str(target_uri or c_uri),
                grounding_path=_derive_grounding_path(op),
                status="QUARANTINED",
            )

        staged_records.append(
            (
                f"rev-{proj_tag}-{cand_id}",
                cand_id,
                spec,
                RevisionMetadata(actor="agent", reason=op_type),
            )
        )

    existing_by_id = {
        str(r.get("revision_id")): (str(r.get("rev_kind")), str(r.get("rev_key")))
        for r in ctx.state_store.list_revisions(ctx.project_id)
    }
    for rev_id, cand_id, _spec, _meta in staged_records:
        prev = existing_by_id.get(rev_id)
        if prev is not None and prev != ("staged_candidate", cand_id):
            raise OpenDVError(
                f"Revision '{rev_id}' conflicts with existing {prev[0]} revision.",
                error_code=ErrorCode.REVISION_CONFLICT,
                run_id=ctx.run_id,
            )

    candidate_ids: list[str] = []
    for rev_id, cand_id, spec, rev_meta in staged_records:
        ctx.state_store.save_revision(
            ctx.project_id,
            revision_id=rev_id,
            rev_kind="staged_candidate",
            rev_key=cand_id,
            parent_rev_id=None,
            payload=spec,
            metadata=rev_meta,
        )
        candidate_ids.append(cand_id)

    resp = DeltaStageResp(
        staged_count=len(candidate_ids),
        candidate_ids=candidate_ids,
        diagnostics=[],
    )
    ctx.coord().emit(
        "TOOL_RESULT",
        {
            "tool_name": "propose_ontology_delta",
            "ok": True,
            "summary": f"staged {len(candidate_ids)} candidates",
        },
    )
    return resp


async def coord_ask_domain_clarification(
    ctx: BrokerToolContext, questions: Sequence[ClarificationQuestion]
) -> HoldAck:
    """Drain active scouts, checkpoint HOLD_CHECKPOINTED, and release active_job_lease."""
    hc = ctx.coord()
    if hc.hold_requested:
        raise OpenDVError(
            "Cannot ask domain clarification after HOLD_REQUESTED.",
            error_code=ErrorCode.HOLD_CHECKPOINTED,
            run_id=ctx.run_id,
        )
    q_dicts = [q.model_dump(mode="json") for q in questions]
    ctx.budget_tracker.check_and_consume(q_dicts, tool_name="ask_domain_clarification")
    hc.hold_requested = True
    hc.emit("HOLD_REQUESTED", {"pending_questions": q_dicts})
    await hc.wait_for_drain()

    checkpoint_seq = hc.sync_seq() + 1
    cp = RunCheckpointRecord(
        run_id=ctx.run_id,
        step="step4",
        phase="S4-HOLD",
        status="HOLD_CHECKPOINTED",
        last_seq=checkpoint_seq,
        ontology_rev_id=ctx.ontology_rev_id,
        sources_fingerprint=ctx.sources_fingerprint,
        pending_questions=list(questions),
        budget_turns_used=ctx.budget_tracker.calls_used,
        budget_tokens_used=0,
        token_count_source="estimated",
        resume_payload={
            "pending_question_ids": [q.question_id for q in questions],
            "discovery_mode": ctx.discovery_mode,
        },
    )
    ctx.state_store.append_event_or_receipt(
        ctx.project_id,
        record_kind="run_checkpoint",
        run_id=ctx.run_id,
        payload=cp,
    )
    ctx.state_store.release_lease(
        ctx.project_id,
        lease_key=f"active_job_lease:{ctx.project_id}",
        holder_run_id=ctx.run_id,
    )
    seq = hc.emit(
        "HOLD_CHECKPOINTED",
        {
            "pending_questions": q_dicts,
            "last_seq": checkpoint_seq,
        },
    )
    return HoldAck(
        run_id=ctx.run_id,
        checkpoint_seq=seq,
        status="HOLD_CHECKPOINTED",
        pending_question_ids=[q.question_id for q in questions],
    )


async def coord_dispatch_scout(
    ctx: BrokerToolContext,
    scout_kind: Literal["table_cluster", "page_range"],
    target_ids: Sequence[str],
) -> list[ScoutDigest]:
    """Dispatch isolated Step 4 scouts via ScoutToolDispatcher with drain tracking."""
    hc = ctx.coord()
    if hc.hold_requested:
        raise OpenDVError(
            "Cannot dispatch scouts after HOLD_REQUESTED.",
            error_code=ErrorCode.HOLD_CHECKPOINTED,
            run_id=ctx.run_id,
        )
    ctx.budget_tracker.check_and_consume(
        {"scout_kind": scout_kind, "target_ids": list(target_ids)},
        tool_name="dispatch_scout",
    )
    if ctx.scout_dispatcher is None:
        raise OpenDVError(
            "ScoutToolDispatcher is not configured on JobMcpBroker.",
            error_code=ErrorCode.AGENT_NOT_CONFIGURED,
            run_id=ctx.run_id,
        )
    hc.emit(
        "TOOL_CALL",
        {"tool_name": "dispatch_scout", "args_redacted": f"{scout_kind}:{list(target_ids)}"},
    )
    hc.begin_scout()
    try:
        digests = await ctx.scout_dispatcher.dispatch_scout(
            scout_kind, target_ids, run_id=ctx.run_id
        )
    finally:
        hc.end_scout()

    dumped = [d.model_dump(mode="json") for d in digests]
    ctx.budget_tracker.check_payload_size(dumped, label="dispatch_scout result")
    hc.emit(
        "TOOL_RESULT",
        {"tool_name": "dispatch_scout", "ok": True, "summary": f"{len(digests)} digests"},
    )
    return digests


__all__ = [
    "BrokerBudgetTracker",
    "BrokerHoldCoordinator",
    "BrokerToolContext",
    "coord_ask_domain_clarification",
    "coord_dispatch_scout",
    "coord_propose_ontology_delta",
    "coord_search_concepts",
    "coord_search_fibo",
    "scout_call_external_mcp",
    "scout_inspect_table_cluster",
    "scout_inspect_table_profile",
    "scout_read_document_pages",
    "scout_view_page_image",
]
