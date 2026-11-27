"""Layer-4 agentic grounding orchestrator with fast-path bypass and 1-retry rescue."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from opendv.agents.ports import AgentHarnessPort
from opendv.agents.sdk_runner import SdkHarnessRunner, load_optional_sdk_module
from opendv.grounding.kernel.calibration import CandidateChannelInputs, select_and_calibrate
from opendv.grounding.spi import KnowledgeSource
from opendv.grounding.structured import steward_context as sc
from opendv.grounding.structured.engine import ColumnGroundingEngine, ColumnSymbolicDraft
from opendv.models import grounding as gm
from opendv.models import ontology as om
from opendv.models import ontology_deltas as od
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import GroundingReceipt
from opendv.models.grounding_spi import (
    CalibrationParams,
    RecallHit,
    RerankColumnPacket,
    TableReasoningContext,
    TableRerankAssignment,
    TableRerankResult,
)
from opendv.models.pii_tokenizer import strip_pii_spans
from opendv.models.policy import STEP4_DEFAULT_BUDGET, HarnessBudget
from opendv.models.project import EvolutionLockSpec
from opendv.models.sources import ColumnProfile
from opendv.service.workflows._grounding_reranker import (
    AgentHarnessReranker,
    AsyncRerankFn,
    RerankFn,
    build_projected_attribute,
    chunk_rerank_packets,
    is_foreign_fk_column,
    is_pii_column_result,
    pascal_identifier,
    slugify_identifier,
    validate_table_rerank_result,
)
from opendv.service.workflows._ontology_deltas import index_existing_ontology_for_projection
from opendv.verifier._v6b_ontology_ap import SOURCE_PREFIX_RE

_MAX_CONCURRENT_RERANKS = 4


def _prepare_rescue_packet(
    col_draft: ColumnSymbolicDraft,
    tbl: gm.TableEvidence,
    new_hits: Sequence[RecallHit],
    *,
    source: KnowledgeSource,
    engine: ColumnGroundingEngine,
    query_text: str,
    anchors: list[gm.TableAnchorCandidate],
    paired_columns: list[gm.ColumnPair],
    steward_notes: list[str],
    request: gm.GroundingRequest,
    calibration: CalibrationParams,
) -> RerankColumnPacket:
    td_top = anchors[0].entry_id if anchors else None
    tctx = TableReasoningContext(
        table_name=tbl.table_name, table_id=tbl.table_id, anchor_hypotheses=anchors,
        sibling_summaries=[f"{c.column_name}:{c.data_type}" for c in tbl.columns[:50]],
        paired_columns=paired_columns, steward_notes=steward_notes,
    )
    hit_list = sorted(new_hits, key=lambda h: (-h.raw_score, h.entry_id))[:15]
    evl = source.evaluate_structured_candidates(
        col_draft.column, col_draft.profile, hit_list, table_context=tctx,
    )
    anc_set = set(source.get_ancestors(td_top, max_depth=8)) if td_top else set()
    new_cands: list[CandidateChannelInputs] = sc.build_candidate_channel_inputs(
        source, col_draft.column, col_draft.profile, hit_list,
        {h.entry_id: float(h.raw_score) for h in hit_list}, {}, {r.entry_id: r for r in evl},
        query_text, engine._aligns, anc_on=bool(anchors) and request.policy.enable_table_context,
        top_uri=td_top, top_ancs=anc_set, top_played=engine._played(td_top),
        has_chk=col_draft.base_ctx.has_checksum_pattern,
        has_nochk=col_draft.base_ctx.has_checksumless_pattern,
        is_bool=col_draft.is_boolean_datatype,
    )
    by_id: dict[str, CandidateChannelInputs] = {c.entry_id: c for c in col_draft.candidates}
    for cand in new_cands:
        by_id.setdefault(cand.entry_id, cand)
    col_draft.candidates = list(by_id.values())
    col_draft.symbolic_outcome = select_and_calibrate(
        col_draft.base_ctx, col_draft.candidates, calibration, request.policy,
    )
    pkt = engine._assign_packet(col_draft)
    col_draft.rerank_packet = pkt
    return pkt


async def ground_columns_orchestrated(
    request: gm.GroundingRequest,
    *,
    source: KnowledgeSource,
    harness: AgentHarnessPort | None = None,
    rerank_fn: RerankFn | AsyncRerankFn | None = None,
    calibration: CalibrationParams | None = None,
    budget: HarnessBudget = STEP4_DEFAULT_BUDGET,
) -> gm.GroundingResponse:
    """Run synchronous Stage 0-2 grounding followed by bounded agentic rerank & 1-retry rescue."""
    engine = ColumnGroundingEngine(source, calibration=calibration)
    if request.policy.mode == "deterministic_only":
        return engine.ground(request)
    if rerank_fn is None and harness is None:
        raise OpenDVError(
            "Agentic grounding requires a configured AgentHarnessPort or RerankFn.",
            error_code=ErrorCode.AGENT_NOT_CONFIGURED,
        )
    if isinstance(harness, SdkHarnessRunner):
        load_optional_sdk_module(harness._provider, module_loader=harness._module_loader)

    draft = engine.ground_symbolic(request)
    if all(not pkts for pkts in draft.rerank_packets.values()):
        return engine.finalize(request, draft, rerank_results={})

    sem, turns_used, turn_lock = asyncio.Semaphore(_MAX_CONCURRENT_RERANKS), 0, asyncio.Lock()

    async def _try_reserve_turn() -> bool:
        nonlocal turns_used
        async with turn_lock:
            if turns_used >= budget.max_turns:
                return False
            turns_used += 1
            return True

    active_rerank: RerankFn | AsyncRerankFn = (
        rerank_fn
        if rerank_fn is not None
        else AgentHarnessReranker(
            harness,  # type: ignore[arg-type]
            budget=budget,
            turn_reserve_fn=_try_reserve_turn,
        )
    )

    async def _run_batch(
        tkey: str, tname: str, batch: list[RerankColumnPacket], albls: list[str], notes: list[str],
    ) -> tuple[str, TableRerankResult]:
        if not await _try_reserve_turn():
            raise OpenDVError(
                f"Grounding rerank budget exhausted (max_turns={budget.max_turns}).",
                error_code=ErrorCode.AGENT_EXECUTION_FAILED,
            )
        async with sem:
            raw = active_rerank(tname, batch, anchor_labels=albls, steward_notes=notes)
            res = await raw if inspect.isawaitable(raw) else raw
        return tkey, validate_table_rerank_result(batch, res)

    for n in request.global_steward_notes:
        if cleaned := strip_pii_spans(n, strip_placeholders=True).strip():
            for td in draft.table_drafts.values():
                if cleaned not in td.steward_notes:
                    td.steward_notes.append(cleaned)

    tables_by_key = {(t.table_id or t.table_name): t for t in request.tables}
    pass1_tasks = [
        _run_batch(
            tkey,
            draft.table_drafts[tkey].table_name,
            batch,
            [a.label for a in draft.table_drafts[tkey].anchors],
            draft.table_drafts[tkey].steward_notes,
        )
        for tkey, pkts in draft.rerank_packets.items()
        if pkts
        for batch in chunk_rerank_packets(pkts)
    ]
    assignments_by_table: dict[str, dict[str, TableRerankAssignment]] = {}
    for tkey, batch_res in await asyncio.gather(*pass1_tasks):
        amap = assignments_by_table.setdefault(tkey, {})
        for asgn in batch_res.assignments:
            amap[asgn.column_name] = asgn

    rescue_tasks: list[Awaitable[tuple[str, TableRerankResult]]] = []
    for tkey, tbl in tables_by_key.items():
        td, amap = draft.table_drafts[tkey], assignments_by_table.get(tkey, {})
        if not amap:
            continue
        rescue_pkts: list[RerankColumnPacket] = []
        for col_draft in td.column_drafts:
            p1_asgn: TableRerankAssignment | None = amap.get(col_draft.column.column_name)
            terms = (
                [t.strip() for t in p1_asgn.suggested_search_terms if t.strip()][:3]
                if p1_asgn else []
            )
            if not p1_asgn or not terms:
                continue
            p1_out = select_and_calibrate(
                replace(
                    col_draft.base_ctx,
                    rerank_assignment=p1_asgn,
                    rerank_expected=True,
                    is_fast_pathed=False,
                ),
                col_draft.candidates, engine._calibration, request.policy,
            )
            if p1_out.status not in ("NO_MATCH", "AMBIGUOUS"):
                continue
            existing_ids = {c.entry_id for c in col_draft.candidates}
            new_hits: dict[str, RecallHit] = {}
            for term in terms:
                for hit in source.recall_by_text(term, top_k=15):
                    prev_h = new_hits.get(hit.entry_id)
                    if hit.entry_id not in existing_ids and (
                        prev_h is None or hit.raw_score > prev_h.raw_score
                    ):
                        new_hits[hit.entry_id] = hit
            if new_hits:
                rescue_pkts.append(_prepare_rescue_packet(
                    col_draft, tbl, list(new_hits.values()),
                    source=source, engine=engine, query_text=" ".join(terms),
                    anchors=td.anchors, paired_columns=td.paired_columns,
                    steward_notes=td.steward_notes, request=request,
                    calibration=engine._calibration,
                ))
        if rescue_pkts:
            albls = [a.label for a in td.anchors]
            rescue_tasks.extend(
                _run_batch(tkey, td.table_name, b, albls, td.steward_notes)
                for b in chunk_rerank_packets(rescue_pkts)
            )

    if rescue_tasks:
        for tkey, batch_res in await asyncio.gather(*rescue_tasks):
            amap = assignments_by_table.setdefault(tkey, {})
            for asgn in batch_res.assignments:
                amap[asgn.column_name] = asgn

    final_rerank = {
        tkey: TableRerankResult(assignments=list(amap.values()))
        for tkey, amap in assignments_by_table.items()
    }
    return engine.finalize(request, draft, rerank_results=final_rerank)


def _sid(tid: str) -> str:
    return tid.split(":", 1)[0] if ":" in tid else "source"


@dataclass(slots=True)
class ProjectionState:
    """Mutable pre-indexed Graph A and receipt accumulator for 4-step projection."""

    ont: om.GraphAOntology
    resp: gm.GroundingResponse
    cps: dict[tuple[str, str], ColumnProfile]
    tmap: Mapping[str, str]
    rcpts: Mapping[str, GroundingReceipt]
    locks: Sequence[EvolutionLockSpec]
    ns: str = ""
    cn: dict[str, str] = field(default_factory=dict)
    pby: dict[str, str] = field(default_factory=dict)
    s2u: dict[str, str] = field(default_factory=dict)
    n2u: dict[str, str] = field(default_factory=dict)
    ats: dict[str, set[str]] = field(default_factory=dict)
    bds: set[tuple[str, str, str, str]] = field(default_factory=set)
    ens: dict[str, set[str]] = field(default_factory=dict)
    s2e: dict[str, str] = field(default_factory=dict)
    rus: set[str] = field(default_factory=set)
    tgs: dict[str, list[str]] = field(default_factory=dict)
    cns: dict[str, dict[str, str | list[str]]] = field(default_factory=dict)
    lhb: dict[str, set[str]] = field(default_factory=dict)
    llk: set[str] = field(default_factory=set)
    dl: list[od.OntologyDelta] = field(default_factory=list)
    chg: set[str] = field(default_factory=set)
    tc: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.ns = self.ont.domain_namespace or "domain"
        (
            self.cn, self.pby, self.s2u, self.n2u, self.ats, self.bds,
            self.ens, self.s2e, self.rus, self.tgs, self.cns, self.lhb, self.llk,
        ) = index_existing_ontology_for_projection(self.ont, self.locks, slugify_identifier)

    def tag(self, u: str, t: str) -> None:
        if t not in (lst := self.tgs.setdefault(u, [])):
            lst.append(t)
        self.chg.add(u)

    def con(self, u: str, k: str, v: str) -> None:
        d = self.cns.setdefault(u, {})
        p = d.get(k)
        if isinstance(p, list) and v not in p:
            d[k] = [*p, v]
        elif isinstance(p, str) and p != v:
            d[k] = [p, v]
        elif p is None:
            d[k] = [v]
        self.chg.add(u)

    def bind(self, u: str, tid: str, col: str) -> None:
        bk = (u, _sid(tid), tid, col)
        if bk not in self.bds:
            self.bds.add(bk)
            bnd = om.ConceptTableBinding(source_id=_sid(tid), table_id=tid, col_name=col)
            self.dl.append(od.AddTableBinding(concept_uri=u, binding=bnd))

    def put_attr(self, u: str, a: om.ConceptAttribute, *, pii: bool = False) -> None:
        if pii:
            self.tag(u, "PII_ISOLATION")
        aset = self.ats.setdefault(u, set())
        if a.name not in aset:
            aset.add(a.name)
            self.dl.append(od.AddAttribute(concept_uri=u, attribute=a))
        elif not (u in self.lhb and a.name in self.lhb[u]):
            meta = a.model_dump(exclude={"name", "is_business_key"})
            self.dl.append(od.UpdateAttributeMetadata(concept_uri=u, attribute_name=a.name, **meta))

    def put_enum(self, eu: str, nm: str, tn: str, vals: Sequence[str], su: str | None) -> None:
        codes = {str(v): str(v) for v in vals[:50]} or {"DEFAULT": "Default"}
        if eu in self.ens:
            for k, v in codes.items():
                if k not in self.ens[eu]:
                    self.ens[eu].add(k)
                    self.dl.append(od.AddEnumerationCode(enum_uri=eu, code=k, description=v))
        else:
            self.ens[eu] = set(codes)
            if su:
                self.s2e[su] = eu
            ref_tbl = f"ref_{slugify_identifier(tn)}"
            self.dl.append(od.AddEnumeration(enumeration=om.OntologyEnumeration(
                enum_uri=eu, enumeration_name=nm, target_reference_table=ref_tbl,
                codes=codes, description=f"Enum {nm}.", standard_uri=su,
            )))

    def add_rel(self, ru: str, **kw: object) -> None:
        if ru not in self.rus:
            self.rus.add(ru)
            rel = om.OntologyRelationship(relationship_uri=ru, **kw)  # type: ignore[arg-type]
            self.dl.append(od.AddRelationship(relationship=rel))

    def ext_map(self, a: gm.TableAnchorCandidate, tid: str) -> om.ExternalMapping | None:
        qc, rid = a.to_quantized_confidence(), a.grounding_receipt_id
        if rid is not None:
            r = self.rcpts.get(rid)
            ok = (
                r and r.provenance == "server_verified" and r.target_kind == "table_anchor"
                and r.table_id == tid and r.standard_uri == a.entry_id and r.confidence == qc
                and r.mapping_type in {"skos:exactMatch", "skos:closeMatch"}
            )
            if not ok:
                return None
        return om.ExternalMapping(
            standard_uri=a.entry_id, reference_pack_id=self.resp.knowledge_source_id,
            reference_pack_sha256=self.resp.knowledge_source_sha256,
            mapping_type=a.suggested_predicate, confidence=qc,  # type: ignore[arg-type]
            grounding_receipt_id=rid,
        )

    def fold_or_new(
        self, tid: str, nm: str, kc: str | None,
        mp: om.ExternalMapping | None = None, pb: str | None = None,
    ) -> str:
        sn = slugify_identifier(nm)
        if u := ((mp and self.s2u.get(mp.standard_uri)) or self.n2u.get(sn)):
            if kc:
                self.bind(u, tid, kc)
            return u
        u = f"urn:opendv:{self.ns}:{pascal_identifier(nm)}"
        cs: dict[str, str | list[str]] = {"played_by_uri": pb} if pb else {}
        bs = [om.ConceptTableBinding(source_id=_sid(tid), table_id=tid, col_name=kc)] if kc else []
        if kc:
            self.bds.add((u, _sid(tid), tid, kc))
        nc = om.CanonicalBusinessConcept(
            concept_uri=u, concept_name=nm, description=f"{nm} business concept.",
            key_authority_tier="TIER_3_LOCAL_SCOPED", governance_constraints=cs,
            external_mappings=[mp] if mp else [], table_bindings=bs,
        )
        self.cn[u], self.n2u[sn] = nm, u
        self.ats.setdefault(u, set())
        if mp and mp.mapping_type in {"skos:exactMatch", "skos:closeMatch"}:
            self.s2u[mp.standard_uri] = u
        if pb:
            self.pby[pb] = u
        if cs:
            self.cns.setdefault(u, dict(cs))
        self.dl.append(od.AddConcept(**nc.model_dump()))
        return u

    def target_of(
        self, c: gm.ColumnGroundingResult, cp: ColumnProfile | None, tid: str,
    ) -> str | None:
        sem = c.choices[0].semantic_context if c.choices else None
        cand = (
            (sem.played_by_uri if sem else None)
            or (sem.target_entity_uri if sem else None)
            or (sem.identified_entity_uri if sem else None)
        )
        if cand and (cand in self.cn or cand in self.s2u):
            return cand if cand in self.cn else self.s2u[cand]
        if cp and cp.fk_ref:
            rt = cp.fk_ref.split("(", 1)[0].split(".", 1)[0].strip()
            rtid = self.tmap.get(rt) or rt
            if rtid in self.tc:
                return self.tc[rtid]
            if (rn := slugify_identifier(SOURCE_PREFIX_RE.sub("", rt))) in self.n2u:
                return self.n2u[rn]
        if not cand:
            return None
        lbl = pascal_identifier(cand.rsplit("/", 1)[-1].rsplit(":", 1)[-1])
        return self.fold_or_new(tid, lbl, None)


def emit_junction_relationships(
    st: ProjectionState,
    juncs: Sequence[tuple[
        gm.TableGroundingResult, str, list[tuple[gm.ColumnGroundingResult, ColumnProfile | None]],
    ]],
    r10s: Mapping[str, list[str]],
    r10m: Mapping[str, list[str]],
) -> None:
    """Emit Hub-less junction AddRelationship deltas after non-junction concepts resolve."""
    for tbl, tid, ccps in juncs:
        r10 = set(r10s.get(tid, [])) | set(r10m.get(tid, []))
        fks = [
            (c, cp) for c, cp in ccps
            if c.column_name not in r10 and is_foreign_fk_column(c.column_name, cp)
        ]
        la = [
            build_projected_attribute(c, cp, is_bk=False, is_pii=is_pii_column_result(c, cp))
            for c, cp in ccps if (c, cp) not in fks and c.column_name not in r10
        ]
        if len(fks) >= 2 and (sc := st.target_of(fks[0][0], fks[0][1], tid)):
            bs = slugify_identifier(SOURCE_PREFIX_RE.sub("", tbl.table_name))
            has_ts = bool({c.column_name.lower() for c, _ in ccps} & {"event_dts", "occurred_at"})
            pf = "t_lnk" if has_ts else "lnk"
            for idx, (fk_c, fk_cp) in enumerate(fks[1:], 1):
                if dc := st.target_of(fk_c, fk_cp, tid):
                    fk_slug = slugify_identifier(fk_c.column_name)
                    is_hier = sc == dc and fk_c.column_name.lower().startswith(
                        ("parent_", "child_", "manager_", "ultimate_"),
                    )
                    tl = f"hal_{bs}" if is_hier else f"{pf}_{bs}"
                    tgt_lnk = f"{tl}_{fk_slug}" if tl in st.llk else tl
                    st.add_rel(
                        f"urn:opendv:{st.ns}:rel:{slugify_identifier(tid)}_{fk_slug}",
                        relationship_name=pascal_identifier(fk_c.column_name),
                        source_concept_uri=sc, target_concept_uri=dc, cardinality="MANY_TO_MANY",
                        target_link=tgt_lnk, grain_semantics=f"Junction edge for {tbl.table_name}",
                        description=f"Junction link via {fk_c.column_name}", source_table_id=tid,
                        source_col_name=fk_c.column_name,
                        source_anchor_col_name=fks[0][0].column_name,
                        link_attributes=la if idx == 1 else [],
                    )


__all__ = [
    "ProjectionState",
    "emit_junction_relationships",
    "ground_columns_orchestrated",
]
