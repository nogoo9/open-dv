"""Internal helpers for Step 6 CEGAR: sharded synthesis, controls, checkpoints, and events."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from opendv.agents.pack_loader import load_agent_pack
from opendv.agents.ports import (
    AgentHarnessPort,
    AgentTurnRequest,
    AgentTurnResult,
    ControlSignalReader,
    RunCheckpointStore,
    RunEventSink,
)
from opendv.models.diagnostics import ClarificationQuestion, HorizonQuestion
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import (
    ControlSignalRecord,
    RunCheckpointRecord,
    RunEventType,
    RunStreamEvent,
)
from opendv.models.json_hasher import sha256_jcs
from opendv.models.ontology import GraphAOntology
from opendv.models.policy import CegarPolicy, HarnessBudget
from opendv.models.project import EvolutionLockSpec, OntologyProposalSpec
from opendv.models.sources import SourceProfile, TableProfile
from opendv.models.vault_ir import DraftShardIR, DraftVaultIR
from opendv.models.wire_dtos_discovery import HoldAnswersReq
from opendv.models.wire_dtos_vault import SemanticCriticVerdict
from opendv.verifier.l1_codemods import merge_draft_shards
from opendv.verifier.ports import VerifierReport

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def emit_run_event(
    sink: RunEventSink | None,
    seq_ref: list[int],
    *,
    run_id: str,
    event_type: RunEventType,
    payload: dict[str, Any],
) -> None:
    """Emit a monotonic Step 6 RunStreamEvent if event_sink is attached."""
    if sink is None:
        return
    current_seq_fn = getattr(sink, "current_seq", None)
    if callable(current_seq_fn):
        seq_ref[0] = max(seq_ref[0], int(current_seq_fn(run_id)))
    seq_ref[0] += 1
    sink.emit(
        RunStreamEvent(
            seq=seq_ref[0],
            run_id=run_id,
            ts=datetime.now(tz=UTC).isoformat(),
            event_type=event_type,
            step="step6",
            payload=payload,
        )
    )


def sources_fingerprint(profiles: Sequence[SourceProfile]) -> str:
    """Compute a deterministic fingerprint across Graph B SourceProfile records."""
    return sha256_jcs(
        [
            {"source_id": p.source_id, "schema_fingerprint": p.schema_fingerprint}
            for p in sorted(profiles, key=lambda s: s.source_id)
        ]
    )


def preallocate_canonical_hubs(ontology: GraphAOntology) -> list[str]:
    """Derive deterministic canonical Hub names from Graph A concepts before sharding."""
    hub_names: list[str] = []
    for concept in ontology.concepts:
        slug = _SLUG_RE.sub("_", concept.concept_name.strip().lower()).strip("_")
        if slug:
            hub_names.append(f"hub_{slug}")
    return list(dict.fromkeys(hub_names))


def resolve_cluster_assignments(
    profiles: Sequence[SourceProfile],
    clusters: Sequence[tuple[str, Sequence[str]]],
) -> list[tuple[str, list[TableProfile]]]:
    """Resolve (cluster_id, tables) batches for sharded Step 6 synthesis."""
    table_by_id: dict[str, TableProfile] = {}
    table_by_name: dict[str, TableProfile] = {}
    for prof in profiles:
        for tbl in prof.tables:
            table_by_id[tbl.table_id] = tbl
            table_by_name[tbl.table_name] = tbl

    if clusters:
        resolved: list[tuple[str, list[TableProfile]]] = []
        for cid, target_ids in sorted(clusters, key=lambda item: item[0]):
            c_tables: list[TableProfile] = []
            for tid in target_ids:
                matched = table_by_id.get(tid) or table_by_name.get(tid)
                if matched is not None:
                    c_tables.append(matched)
            if c_tables:
                resolved.append((cid, c_tables))
        if resolved:
            return resolved

    all_tables = sorted(table_by_id.values(), key=lambda t: t.table_id)
    chunk_size = 20
    return [
        (f"cluster_{idx:02d}", all_tables[offset : offset + chunk_size])
        for idx, offset in enumerate(range(0, len(all_tables), chunk_size))
    ]


def collect_ontology_proposals(
    turn_result: AgentTurnResult,
    *,
    wave_number: int,
) -> list[OntologyProposalSpec]:
    """Extract uncommitted OntologyProposalSpec entries from harness tool/metadata payloads."""
    proposals: list[OntologyProposalSpec] = []
    for ev in turn_result.tool_events:
        raw_props = ev.get("ontology_proposals")
        if isinstance(raw_props, list):
            for idx, item in enumerate(raw_props):
                if isinstance(item, OntologyProposalSpec):
                    proposals.append(item.model_copy(update={"status": "PENDING_STEWARD"}))
                elif isinstance(item, dict):
                    merged = {"wave_number": wave_number, "status": "PENDING_STEWARD", **item}
                    merged.setdefault("proposal_id", f"prop-w{wave_number}-{idx + 1}")
                    proposals.append(OntologyProposalSpec.model_validate(merged))
    return proposals


def poll_control_signals(
    reader: ControlSignalReader | None,
    *,
    run_id: str,
    last_signal_seq: list[int],
    budget: HarnessBudget,
    hints: list[str],
) -> tuple[bool, bool, bool, HarnessBudget]:
    """Poll ControlSignalReader and return (pause, accept_best, horizon_review, updated_budget)."""
    if reader is None:
        return False, False, False, budget
    signals: Sequence[ControlSignalRecord] = reader.poll_signals(
        run_id,
        after_seq=last_signal_seq[0],
    )
    pause_requested = False
    accept_best = False
    horizon_review = False
    cur_budget = budget
    for sig in signals:
        last_signal_seq[0] += 1
        if sig.action == "pause":
            pause_requested = True
        elif sig.action == "hint" and sig.hint:
            hints.append(sig.hint.strip())
        elif sig.action == "accept-best":
            accept_best = True
        elif sig.action == "horizon-review":
            horizon_review = True
        elif sig.action == "raise-budget":
            cur_budget = cur_budget.model_copy(
                update={
                    "max_turns": cur_budget.max_turns + int(sig.extra_turns or 0),
                    "max_tokens": cur_budget.max_tokens + int(sig.extra_tokens or 0),
                    "timeout_seconds": (
                        cur_budget.timeout_seconds + int(sig.extra_timeout_seconds or 0)
                    ),
                }
            )
    return pause_requested, accept_best, horizon_review, cur_budget


def build_resume_payload(
    base: Mapping[str, str | int | bool | list[str]] | None = None,
    *,
    last_sig_seq: int = 0,
    hints: Sequence[str] = (),
    accept_best_requested: bool = False,
    horizon_review_requested: bool = False,
    effective_budget: HarnessBudget | None = None,
    base_budget: HarnessBudget | None = None,
    v7_score: int | None = None,
) -> dict[str, str | int | bool | list[str]]:
    """Build checkpoint resume_payload preserving signal cursor, hints, flags, and raised budget."""
    payload: dict[str, str | int | bool | list[str]] = dict(base or {})
    if last_sig_seq > 0 or "last_sig_seq" not in payload:
        payload["last_sig_seq"] = last_sig_seq
    if hints:
        payload["hints"] = list(hints)
    if accept_best_requested:
        payload["accept_best_requested"] = True
    if horizon_review_requested:
        payload["horizon_review_requested"] = True
    if (
        effective_budget is not None
        and base_budget is not None
        and effective_budget != base_budget
    ):
        payload["budget_max_turns"] = effective_budget.max_turns
        payload["budget_max_tokens"] = effective_budget.max_tokens
        payload["budget_timeout_seconds"] = effective_budget.timeout_seconds
    if v7_score is not None:
        payload["v7_score"] = v7_score
    return payload


def commit_hold_checkpoint(
    *,
    run_id: str,
    phase: str,
    ontology_version: str,
    src_fingerprint: str,
    pending_questions: Sequence[ClarificationQuestion | HorizonQuestion],
    turns_used: int,
    tokens_used: int,
    token_count_source: str,
    resume_payload: dict[str, str | int | bool | list[str]],
    seq_ref: list[int],
    event_sink: RunEventSink | None,
    checkpoint_store: RunCheckpointStore | None,
) -> RunCheckpointRecord:
    """Emit HOLD_REQUESTED, persist HOLD_CHECKPOINTED, release lease, and notify."""
    q_list = list(pending_questions)
    emit_run_event(
        event_sink,
        seq_ref,
        run_id=run_id,
        event_type="HOLD_REQUESTED",
        payload={"pending_questions": [q.model_dump(mode="json") for q in q_list]},
    )
    tok_src: Literal["reported", "estimated"] = (
        "reported" if token_count_source == "reported" else "estimated"
    )
    hold_seq = seq_ref[0] + (1 if event_sink is not None else 0)
    record = RunCheckpointRecord(
        run_id=run_id,
        step="step6",
        phase=phase,
        status="HOLD_CHECKPOINTED",
        last_seq=hold_seq,
        ontology_rev_id=ontology_version,
        sources_fingerprint=src_fingerprint,
        pending_questions=q_list,
        budget_turns_used=turns_used,
        budget_tokens_used=tokens_used,
        token_count_source=tok_src,
        resume_payload=resume_payload,
    )
    cp_sha = sha256_jcs(record.model_dump(mode="json"))
    if checkpoint_store is not None:
        checkpoint_store.save_checkpoint(record)
        checkpoint_store.release_active_lease(run_id)
    emit_run_event(
        event_sink,
        seq_ref,
        run_id=run_id,
        event_type="HOLD_CHECKPOINTED",
        payload={
            "checkpoint_sha256": cp_sha,
            "pending_questions": [q.model_dump(mode="json") for q in q_list],
            "last_seq": hold_seq,
        },
    )
    return record


def commit_uncertified_checkpoint(
    *,
    run_id: str,
    phase: str,
    ontology_version: str,
    src_fingerprint: str,
    turns_used: int,
    tokens_used: int,
    token_count_source: str,
    best_ir_sha: str | None,
    seq_ref: list[int],
    checkpoint_store: RunCheckpointStore | None,
) -> RunCheckpointRecord:
    """Persist an UNCERTIFIED_DRAFT checkpoint and release active lease."""
    tok_src: Literal["reported", "estimated"] = (
        "reported" if token_count_source == "reported" else "estimated"
    )
    record = RunCheckpointRecord(
        run_id=run_id,
        step="step6",
        phase=phase,
        status="UNCERTIFIED_DRAFT",
        last_seq=seq_ref[0],
        ontology_rev_id=ontology_version,
        sources_fingerprint=src_fingerprint,
        pending_questions=[],
        budget_turns_used=turns_used,
        budget_tokens_used=tokens_used,
        token_count_source=tok_src,
        resume_payload={"best_ir_sha256": best_ir_sha or ""},
    )
    if checkpoint_store is not None:
        checkpoint_store.save_checkpoint(record)
        checkpoint_store.release_active_lease(run_id)
    return record


def build_ship_vault_prompt(
    *,
    project_id: str,
    wave_number: int,
    ontology: GraphAOntology,
    profiles: Sequence[SourceProfile],
    locks: EvolutionLockSpec | None,
    prior_report: VerifierReport | None,
    hints: Sequence[str],
    hold_answers: HoldAnswersReq | None,
    preallocated_hubs: Sequence[str] = (),
    cluster_id: str | None = None,
    cluster_tables: Sequence[TableProfile] = (),
    prior_v7_verdict: SemanticCriticVerdict | None = None,
) -> str:
    """Construct compact structured JSON prompt for @ship-vault full or sharded turn."""
    table_payload = (
        [
            {
                "table_id": t.table_id,
                "table_name": t.table_name,
                "columns": [
                    {
                        "col_name": c.col_name,
                        "sql_type": c.sql_type,
                        "is_pk": c.is_pk,
                        "fk_ref": c.fk_ref,
                        "pii_tags": c.pii_tags,
                    }
                    for c in t.columns
                ],
            }
            for t in cluster_tables
        ]
        if cluster_tables
        else [
            {
                "source_id": p.source_id,
                "tables": [
                    {
                        "table_id": t.table_id,
                        "table_name": t.table_name,
                        "columns": [c.col_name for c in t.columns],
                    }
                    for t in p.tables
                ],
            }
            for p in profiles
        ]
    )
    payload: dict[str, Any] = {
        "project_id": project_id,
        "wave_number": wave_number,
        "ontology_version": ontology.ontology_version,
        "concepts": [c.concept_name for c in ontology.concepts],
        "preallocated_hubs": list(preallocated_hubs),
        "evolution_locks": locks.model_dump(mode="json") if locks else None,
        "tables": table_payload,
        "hints": list(hints),
        "hold_answers": hold_answers.answers if hold_answers else {},
    }
    if cluster_id is not None:
        payload["cluster_id"] = cluster_id
    if prior_report is not None:
        payload["prior_diagnostics"] = [
            d.model_dump(mode="json") for d in prior_report.all_diagnostics[:25]
        ]
    if prior_v7_verdict is not None:
        payload["prior_v7_verdict"] = {
            "score": prior_v7_verdict.score,
            "findings": list(prior_v7_verdict.findings),
            "accepted_waiver_ids": list(prior_v7_verdict.accepted_waiver_ids),
        }
    return json.dumps(payload, separators=(",", ":"))


async def run_ship_vault_synthesis(
    *,
    run_id: str,
    project_id: str,
    wave_number: int,
    attempt: int,
    ontology: GraphAOntology,
    profiles: Sequence[SourceProfile],
    clusters: Sequence[tuple[str, Sequence[str]]],
    locks: EvolutionLockSpec | None,
    prior_report: VerifierReport | None,
    hints: Sequence[str],
    hold_answers: HoldAnswersReq | None,
    policy: CegarPolicy,
    budget: HarnessBudget,
    harness: AgentHarnessPort,
    model_overrides: Mapping[str, str] | None = None,
    event_sink: RunEventSink | None = None,
    prior_v7_verdict: SemanticCriticVerdict | None = None,
) -> tuple[DraftVaultIR, list[OntologyProposalSpec], int, int, str]:
    """Execute S6-02 @ship-vault (sharded per cluster when total tables > 40)."""
    pack = load_agent_pack("ship-vault")
    total_tables = sum(len(p.tables) for p in profiles)
    preallocated_hubs = preallocate_canonical_hubs(ontology)
    proposals: list[OntologyProposalSpec] = []
    turns = 0
    tokens = 0
    tok_source = "estimated"
    ovr = model_overrides or {}
    ship_override = (ovr.get("ship-vault") or ovr.get("STANDARD_WORKER") or "").strip() or None

    if total_tables > 40:
        assignments = resolve_cluster_assignments(profiles, clusters)
        shards: list[DraftShardIR] = []
        for cid, c_tables in assignments:
            prompt = build_ship_vault_prompt(
                project_id=project_id,
                wave_number=wave_number,
                ontology=ontology,
                profiles=profiles,
                locks=locks,
                prior_report=prior_report,
                hints=hints,
                hold_answers=hold_answers,
                preallocated_hubs=preallocated_hubs,
                cluster_id=cid,
                cluster_tables=c_tables,
                prior_v7_verdict=prior_v7_verdict,
            )
            res = await harness.run_turn(
                AgentTurnRequest(
                    run_id=run_id,
                    agent_id="ship-vault",
                    step="step6",
                    prompt=prompt,
                    system_instructions=pack.markdown_body,
                    output_schema=DraftShardIR,
                    model_tier="STANDARD_WORKER",
                    model_override=ship_override,
                    cegar_policy=policy,
                    role_kind="none",
                    allowed_tools=[],
                    budget=budget,
                    attempt=attempt,
                    cluster_id=cid,
                    metadata={
                        "model_overrides": dict(model_overrides or {}),
                        "preallocated_hubs": preallocated_hubs,
                        "sharded": True,
                    },
                ),
                event_sink=event_sink,
            )
            turns += res.turns_used
            tokens += res.total_tokens
            if res.token_count_source == "reported":
                tok_source = "reported"
            shard = (
                res.structured_output
                if isinstance(res.structured_output, DraftShardIR)
                else DraftShardIR.model_validate(res.structured_output)
            )
            shards.append(shard)
            proposals.extend(collect_ontology_proposals(res, wave_number=wave_number))

        merged = merge_draft_shards(
            shards,
            project_id=project_id,
            ontology_version=ontology.ontology_version,
            wave_number=wave_number,
        )
        return merged, proposals, turns, tokens, tok_source

    prompt = build_ship_vault_prompt(
        project_id=project_id,
        wave_number=wave_number,
        ontology=ontology,
        profiles=profiles,
        locks=locks,
        prior_report=prior_report,
        hints=hints,
        hold_answers=hold_answers,
        preallocated_hubs=preallocated_hubs,
        prior_v7_verdict=prior_v7_verdict,
    )
    res = await harness.run_turn(
        AgentTurnRequest(
            run_id=run_id,
            agent_id="ship-vault",
            step="step6",
            prompt=prompt,
            system_instructions=pack.markdown_body,
            output_schema=DraftVaultIR,
            model_tier="STANDARD_WORKER",
            model_override=ship_override,
            cegar_policy=policy,
            role_kind="none",
            allowed_tools=[],
            budget=budget,
            attempt=attempt,
            metadata={
                "model_overrides": dict(model_overrides or {}),
                "preallocated_hubs": preallocated_hubs,
                "sharded": False,
            },
        ),
        event_sink=event_sink,
    )
    turns += res.turns_used
    tokens += res.total_tokens
    tok_source = res.token_count_source
    draft = (
        res.structured_output
        if isinstance(res.structured_output, DraftVaultIR)
        else DraftVaultIR.model_validate(res.structured_output)
    )
    proposals.extend(collect_ontology_proposals(res, wave_number=wave_number))
    return draft, proposals, turns, tokens, tok_source


def enforce_accept_best_gate(
    *,
    policy: CegarPolicy,
    v1_v6_fail: int,
    v6b_fail: int,
    v7_score: int | None,
) -> None:
    """Enforce strict accept-best gate checks (raising VERIFICATION_GATE_FAILED if unmet)."""
    if policy == "strict" and (v6b_fail > 0 or (v7_score is None or v7_score < 95)):
        raise OpenDVError(
            "Strict policy forbids accept-best when |V6b_unwaived| > 0 or V7 < 95.",
            error_code=ErrorCode.VERIFICATION_GATE_FAILED,
        )
    if v1_v6_fail > 0 and policy == "strict":
        raise OpenDVError(
            "Strict policy forbids accept-best while symbolic gates V1-V6 fail.",
            error_code=ErrorCode.VERIFICATION_GATE_FAILED,
        )
