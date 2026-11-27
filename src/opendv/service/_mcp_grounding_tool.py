"""Step-4 synchronous MCP ground_columns tool (<= 64 KB, server-minted receipts)."""

from __future__ import annotations

import json
from typing import Any

from opendv.grounding.kernel.overlay import OverlayKnowledgeSource
from opendv.grounding.providers.fibo.knowledge_source import FiboKnowledgeSource
from opendv.grounding.structured.engine import ColumnGroundingEngine
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import GroundingReceipt
from opendv.models.grounding import (
    ColumnGroundingResult,
    GroundingPolicy,
    GroundingResponse,
    TableAnchorCandidate,
    TableGroundingResult,
)
from opendv.models.ontology import GraphAOntology
from opendv.models.ontology_deltas import OntologyDelta
from opendv.models.project import ConceptMemorySpec, EvolutionLockSpec
from opendv.models.sources import SourceProfile, TableProfile
from opendv.service._mcp_tools import BrokerToolContext
from opendv.service.workflows._grounding_projection import project_grounding_to_deltas
from opendv.sources.grounding_adapter import source_profiles_to_grounding_request

_MAX_MCP_GROUND_BYTES = 65536
_ELIGIBLE_PREDICATES = frozenset({"skos:exactMatch", "skos:closeMatch"})


def _json_bytes(payload: Any) -> int:
    return len(json.dumps(payload, default=str, separators=(",", ":")).encode("utf-8"))


def _parse_steward_notes(
    steward_notes: dict[str, str] | None,
) -> tuple[dict[str, list[str]], dict[tuple[str, str], str], list[str]]:
    table_notes: dict[str, list[str]] = {}
    col_notes: dict[tuple[str, str], str] = {}
    global_notes: list[str] = []
    if not steward_notes:
        return table_notes, col_notes, global_notes
    for raw_key, raw_val in steward_notes.items():
        key = str(raw_key).strip()
        val = str(raw_val)
        if not key or not val.strip():
            continue
        if key in ("global", "*"):
            global_notes.append(val)
        elif "." in key:
            tbl_part, col_part = key.split(".", 1)
            col_notes[(tbl_part.strip(), col_part.strip())] = val
        else:
            table_notes.setdefault(key, []).append(val)
    return table_notes, col_notes, global_notes


def _hydrate_project_state(
    ctx: BrokerToolContext, domain_namespace: str
) -> tuple[GraphAOntology, list[ConceptMemorySpec], list[EvolutionLockSpec]]:
    ont_revs = ctx.state_store.list_revisions(ctx.project_id, rev_kind="ontology_version")
    if ont_revs:
        raw_ont = ont_revs[-1].get("payload", ont_revs[-1].get("payload_json"))
        existing_ont = (
            raw_ont
            if isinstance(raw_ont, GraphAOntology)
            else GraphAOntology.model_validate(raw_ont)
        )
    else:
        existing_ont = GraphAOntology(
            ontology_version="0.0.0", domain_namespace=domain_namespace
        )

    mem_specs: list[ConceptMemorySpec] = []
    for row in ctx.state_store.list_revisions(ctx.project_id, rev_kind="concept_memory"):
        raw_m = row.get("payload", row.get("payload_json"))
        if raw_m is not None:
            mem_specs.append(
                raw_m
                if isinstance(raw_m, ConceptMemorySpec)
                else ConceptMemorySpec.model_validate(raw_m)
            )

    locks: list[EvolutionLockSpec] = []
    for row in ctx.state_store.list_revisions(ctx.project_id, rev_kind="evolution_lock"):
        raw_l = row.get("payload", row.get("payload_json"))
        if raw_l is not None:
            locks.append(
                raw_l
                if isinstance(raw_l, EvolutionLockSpec)
                else EvolutionLockSpec.model_validate(raw_l)
            )
    return existing_ont, mem_specs, locks


def _mint_receipts_for_response(
    ctx: BrokerToolContext,
    response: GroundingResponse,
    *,
    table_id_map: dict[str, str],
    table_fp_map: dict[str, str],
) -> tuple[GroundingResponse, dict[str, GroundingReceipt]]:
    receipts_by_id: dict[str, GroundingReceipt] = {}
    updated_tables: list[TableGroundingResult] = []

    for tbl_res in response.tables:
        tid = (
            table_id_map.get(tbl_res.table_id or tbl_res.table_name)
            or tbl_res.table_id
            or table_id_map.get(tbl_res.table_name)
            or tbl_res.table_name
        )
        fp = table_fp_map.get(tid, "")
        updated_anchors: list[TableAnchorCandidate] = []
        for anc in tbl_res.inferred_table_anchors:
            if (
                fp
                and tid
                and anc.origin_kind == "reference_standard"
                and anc.suggested_predicate in _ELIGIBLE_PREDICATES
            ):
                rcpt = GroundingReceipt(
                    target_kind="table_anchor",
                    table_id=tid,
                    source_column=None,
                    schema_fingerprint=fp,
                    standard_uri=anc.entry_id,
                    reference_pack_id=response.knowledge_source_id,
                    reference_pack_sha256=response.knowledge_source_sha256,
                    confidence=anc.to_quantized_confidence(),
                    mapping_type=anc.suggested_predicate,
                    table_anchor_uri=anc.entry_id,
                    provenance="server_verified",
                )
                ctx.state_store.append_event_or_receipt(
                    ctx.project_id,
                    record_kind="grounding_receipt",
                    run_id=ctx.run_id,
                    payload=rcpt,
                )
                receipts_by_id[rcpt.receipt_id] = rcpt
                updated_anchors.append(
                    anc.model_copy(update={"grounding_receipt_id": rcpt.receipt_id})
                )
            else:
                updated_anchors.append(anc)

        top_anchor_uri = updated_anchors[0].entry_id if updated_anchors else None
        updated_cols: list[ColumnGroundingResult] = []
        for col_res in tbl_res.columns:
            top = col_res.choices[0] if col_res.choices else None
            if (
                fp
                and tid
                and top is not None
                and col_res.status == "MATCHED"
                and top.origin_kind == "reference_standard"
                and top.suggested_predicate in _ELIGIBLE_PREDICATES
            ):
                rcpt = GroundingReceipt(
                    target_kind="column",
                    table_id=tid,
                    source_column=col_res.column_name,
                    schema_fingerprint=fp,
                    standard_uri=top.entry_id,
                    reference_pack_id=response.knowledge_source_id,
                    reference_pack_sha256=response.knowledge_source_sha256,
                    confidence=top.to_quantized_confidence(),
                    mapping_type=top.suggested_predicate,
                    table_anchor_uri=top_anchor_uri,
                    provenance="server_verified",
                )
                ctx.state_store.append_event_or_receipt(
                    ctx.project_id,
                    record_kind="grounding_receipt",
                    run_id=ctx.run_id,
                    payload=rcpt,
                )
                receipts_by_id[rcpt.receipt_id] = rcpt
                updated_cols.append(
                    col_res.model_copy(update={"grounding_receipt_id": rcpt.receipt_id})
                )
            else:
                updated_cols.append(col_res)

        updated_tables.append(
            tbl_res.model_copy(
                update={
                    "table_id": tbl_res.table_id or tid,
                    "inferred_table_anchors": updated_anchors,
                    "columns": updated_cols,
                }
            )
        )

    return response.model_copy(update={"tables": updated_tables}), receipts_by_id


def _partition_deltas_by_table(
    deltas: list[OntologyDelta],
    table_keys: list[str],
) -> dict[str, list[dict[str, Any]]]:
    by_table: dict[str, list[dict[str, Any]]] = {k: [] for k in table_keys}
    if not table_keys:
        return by_table
    concept_to_tid: dict[str, str] = {}
    enum_to_tid: dict[str, str] = {}
    for op in deltas:
        if op.op_type == "AddConcept" and op.table_bindings:
            concept_to_tid.setdefault(op.concept_uri, op.table_bindings[0].table_id)
        elif op.op_type == "AddTableBinding":
            concept_to_tid.setdefault(op.concept_uri, op.binding.table_id)
    for op in deltas:
        cu = getattr(op, "concept_uri", None)
        if op.op_type == "AddAttribute" and op.attribute.enum_uri and cu in concept_to_tid:
            enum_to_tid.setdefault(op.attribute.enum_uri, concept_to_tid[cu])
        elif op.op_type == "UpdateAttributeMetadata" and op.enum_uri and cu in concept_to_tid:
            enum_to_tid.setdefault(op.enum_uri, concept_to_tid[cu])

    fallback_tid = table_keys[0]
    for op in deltas:
        target_tid: str | None = None
        if op.op_type == "AddConcept" and op.table_bindings:
            target_tid = op.table_bindings[0].table_id
        elif op.op_type == "AddTableBinding":
            target_tid = op.binding.table_id
        elif op.op_type == "AddRelationship" and op.relationship.source_table_id:
            target_tid = op.relationship.source_table_id
        elif op.op_type == "AddEnumeration":
            target_tid = enum_to_tid.get(op.enumeration.enum_uri)
        elif op.op_type == "AddEnumerationCode":
            target_tid = enum_to_tid.get(op.enum_uri)
        else:
            cu = getattr(op, "concept_uri", None)
            if isinstance(cu, str):
                target_tid = concept_to_tid.get(cu)
        chosen = target_tid if target_tid in by_table else fallback_tid
        by_table[chosen].append(op.model_dump(mode="json"))
    return by_table


def _serialize_choice(ch: Any) -> dict[str, Any]:
    return {
        "entry_id": ch.entry_id,
        "label": ch.label,
        "entry_kind": ch.entry_kind,
        "origin_kind": ch.origin_kind,
        "point": ch.confidence.point,
        "lower": ch.confidence.lower,
        "upper": ch.confidence.upper,
        "predicate": ch.suggested_predicate,
    }


def _apply_size_cascade(
    payload: dict[str, Any],
    *,
    column_offset: int,
    limit: int,
) -> None:
    if _json_bytes(payload) <= limit:
        return
    # Stage 1: Drop runner_ups (preserve runner_ups_count)
    for tbl in payload["tables"]:
        for col in tbl["columns"]:
            col["runner_ups"] = []
    if _json_bytes(payload) <= limit:
        return
    # Stage 2: Truncate steward_summary <= 160 chars
    for tbl in payload["tables"]:
        for col in tbl["columns"]:
            summ = str(col.get("steward_summary") or "")
            if len(summ) > 160:
                col["steward_summary"] = summ[:157] + "..."
    if _json_bytes(payload) <= limit:
        return
    # Stage 3: Omit suggested_deltas in reverse table order
    for tbl in reversed(payload["tables"]):
        if tbl["suggested_deltas"]:
            tbl["suggested_deltas"] = []
            tbl["suggested_deltas_truncated"] = True
            if _json_bytes(payload) <= limit:
                return
    # Stage 4: Paginate excess tables (preserving table order in truncated_table_ids)
    while len(payload["tables"]) > 1 and _json_bytes(payload) > limit:
        popped = payload["tables"].pop()
        payload["truncated_table_ids"].insert(0, popped.get("table_id") or popped["table_name"])
    if _json_bytes(payload) <= limit:
        return
    # Stage 5: Paginate intra-table columns (keep at least 1 column)
    if len(payload["tables"]) == 1:
        tbl = payload["tables"][0]
        while len(tbl["columns"]) > 1 and _json_bytes(payload) > limit:
            tbl["columns"].pop()
            tbl["next_column_offset"] = column_offset + len(tbl["columns"])


def coord_ground_columns(
    ctx: BrokerToolContext,
    *,
    table_ids: list[str] | None = None,
    column_offset: int = 0,
    steward_notes: dict[str, str] | None = None,
    max_choices: int = 3,
) -> dict[str, Any]:
    """Synchronously ground columns for project tables, mint receipts, and enforce <= 64 KB."""
    ctx.budget_tracker.check_and_consume(
        {
            "table_ids": table_ids,
            "column_offset": column_offset,
            "steward_notes": steward_notes,
            "max_choices": max_choices,
        },
        tool_name="ground_columns",
    )
    ctx.coord().emit(
        "TOOL_CALL",
        {
            "tool_name": "ground_columns",
            "args_redacted": f"tables={table_ids},offset={column_offset}",
        },
    )
    if column_offset < 0 or max_choices < 1:
        raise OpenDVError(
            "column_offset must be >= 0 and max_choices must be >= 1.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            run_id=ctx.run_id,
        )

    proj = ctx.state_store.get_project(ctx.project_id)
    source_profiles: list[SourceProfile] = []
    table_profiles: list[TableProfile] = []
    table_fp_map: dict[str, str] = {}
    table_id_map: dict[str, str] = {}
    for src in ctx.state_store.list_sources(ctx.project_id):
        rec = ctx.state_store.get_source_record(ctx.project_id, src.source_id)
        prof_raw = rec.get("profile")
        if prof_raw is None:
            continue
        prof = (
            prof_raw
            if isinstance(prof_raw, SourceProfile)
            else SourceProfile.model_validate(prof_raw)
        )
        if prof.tables:
            source_profiles.append(prof)
            for tbl in prof.tables:
                table_profiles.append(tbl)
                table_fp_map[tbl.table_id] = prof.schema_fingerprint
                table_id_map[tbl.table_id] = tbl.table_id
                table_id_map.setdefault(tbl.table_name, tbl.table_id)

    if table_ids is not None:
        missing_ids = [tid for tid in table_ids if tid not in table_id_map]
        if missing_ids:
            raise OpenDVError(
                f"Table(s) not found in project '{ctx.project_id}': {missing_ids}",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
                run_id=ctx.run_id,
            )

    t_notes, c_notes, g_notes = _parse_steward_notes(steward_notes)
    request = source_profiles_to_grounding_request(
        source_profiles,
        project_state=proj,
        table_ids=set(table_ids) if table_ids is not None else None,
        table_steward_notes=t_notes,
        column_steward_notes=c_notes,
        global_steward_notes=g_notes,
        policy=GroundingPolicy(mode="agentic", max_choices=max_choices),
    )

    if column_offset > 0:
        if len(request.tables) != 1:
            raise OpenDVError(
                "column_offset > 0 requires single-table scope.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                run_id=ctx.run_id,
            )
        if column_offset >= len(request.tables[0].columns):
            raise OpenDVError(
                f"column_offset ({column_offset}) exceeds column count for "
                f"'{request.tables[0].table_name}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                run_id=ctx.run_id,
            )

    existing_ont, mem_specs, locks = _hydrate_project_state(ctx, proj.domain_namespace)
    base_src = ctx.fibo_source or FiboKnowledgeSource(
        loader=getattr(ctx.bm25_matcher, "loader", None)
    )
    overlay = OverlayKnowledgeSource(
        base_src,
        ontology=existing_ont,
        concept_memory=mem_specs,
        steward_answers=ctx.steward_answers,
        discovery_mode=ctx.discovery_mode,
    )
    engine = ColumnGroundingEngine(overlay)
    draft = engine.ground_symbolic(request)
    raw_resp = engine.finalize(request, draft, rerank_results=None)
    updated_resp, receipts_by_id = _mint_receipts_for_response(
        ctx, raw_resp, table_id_map=table_id_map, table_fp_map=table_fp_map
    )

    proj_res = project_grounding_to_deltas(
        updated_resp,
        table_profiles=table_profiles,
        existing_ontology=existing_ont,
        table_id_map=table_id_map,
        receipts_by_id=receipts_by_id,
        evolution_locks=locks,
    )
    resp_keys = [t.table_id or t.table_name for t in updated_resp.tables]
    deltas_by_table = _partition_deltas_by_table(proj_res.suggested_deltas, resp_keys)

    out_tables: list[dict[str, Any]] = []
    for tbl_res in updated_resp.tables:
        tkey = tbl_res.table_id or tbl_res.table_name
        col_entries: list[dict[str, Any]] = []
        for col_res in tbl_res.columns:
            top = _serialize_choice(col_res.choices[0]) if col_res.choices else None
            runners = [_serialize_choice(ch) for ch in col_res.choices[1:]]
            col_entries.append(
                {
                    "column_name": col_res.column_name,
                    "status": col_res.status,
                    "review_reason": col_res.review_reason,
                    "no_match_category": col_res.no_match_category,
                    "grounding_receipt_id": col_res.grounding_receipt_id,
                    "execution_mode_used": col_res.execution_mode_used,
                    "top_choice": top,
                    "runner_ups": runners,
                    "runner_ups_count": len(runners),
                    "steward_summary": col_res.steward_summary,
                }
            )
        sliced_cols = col_entries[column_offset:] if column_offset > 0 else col_entries
        out_tables.append(
            {
                "table_name": tbl_res.table_name,
                "table_id": tbl_res.table_id,
                "anchors": [
                    {
                        "entry_id": anc.entry_id,
                        "label": anc.label,
                        "archetype": anc.archetype,
                        "origin_kind": anc.origin_kind,
                        "confidence": anc.confidence,
                        "suggested_predicate": anc.suggested_predicate,
                        "grounding_receipt_id": anc.grounding_receipt_id,
                        "supporting_columns": list(anc.supporting_columns),
                    }
                    for anc in tbl_res.inferred_table_anchors
                ],
                "paired_columns": [p.model_dump(mode="json") for p in tbl_res.paired_columns],
                "columns": sliced_cols,
                "suggested_deltas": deltas_by_table.get(tkey, []),
                "suggested_deltas_truncated": False,
                "next_column_offset": None,
            }
        )

    payload: dict[str, Any] = {
        "knowledge_source_version": updated_resp.knowledge_source_version,
        "knowledge_source_sha256": updated_resp.knowledge_source_sha256,
        "tables": out_tables,
        "truncated_table_ids": [],
    }
    limit = min(_MAX_MCP_GROUND_BYTES, ctx.budget_tracker.budget.max_tool_payload_bytes)
    _apply_size_cascade(payload, column_offset=column_offset, limit=limit)
    ctx.budget_tracker.check_payload_size(payload, label="ground_columns result")
    ctx.coord().emit(
        "TOOL_RESULT",
        {
            "tool_name": "ground_columns",
            "ok": True,
            "summary": f"{len(payload['tables'])} tables",
        },
    )
    return payload


__all__ = ["coord_ground_columns"]
