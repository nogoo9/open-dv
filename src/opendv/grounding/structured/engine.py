"""Synchronous Module 1A ColumnGroundingEngine (Stage 0-2 symbolic & finalization)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from opendv.grounding.kernel.calibration import (
    CalibrationOutcome,
    CandidateChannelInputs,
    ColumnCalibrationContext,
    select_and_calibrate,
)
from opendv.grounding.spi import KnowledgeSource
from opendv.grounding.structured import steward_context as sc
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.grounding import (
    ColumnEvidence,
    ColumnGroundingResult,
    ColumnPair,
    GroundingExecutionMode,
    GroundingRequest,
    GroundingResponse,
    InferredValueProfile,
    TableAnchorCandidate,
    TableEvidence,
    TableGroundingResult,
)
from opendv.models.grounding_spi import (
    CalibrationParams,
    RecallHit,
    RerankCandidateSummary,
    RerankColumnPacket,
    TableReasoningContext,
    TableRerankResult,
)

_ANCHOR_ARCH: Final[frozenset[str]] = frozenset({
    "entity", "agreement", "event_occurrence", "party_role",
})
_SEQ_RE = re.compile(r"(?i)(?:^line_no$|^seq_no$|^item_idx$|_seq$|_line_no$)")
_CHK_PAT: Final[frozenset[str]] = frozenset({
    *sc.NON_PII_BK_PATTERNS[:5], "iban", "aba_routing",
})
_NOCHK_PAT: Final[frozenset[str]] = frozenset({"swift_bic", "iso_4217_ccy", "iso_3166_country"})


@dataclass(slots=True)
class ColumnSymbolicDraft:
    table_key: str
    table_name: str
    column: ColumnEvidence
    profile: InferredValueProfile
    base_ctx: ColumnCalibrationContext
    candidates: list[CandidateChannelInputs]
    symbolic_outcome: CalibrationOutcome
    fast_path_eligible: bool
    clean_steward_note: str | None
    is_boolean_datatype: bool
    rerank_packet: RerankColumnPacket | None = None


@dataclass(slots=True)
class TableSymbolicDraft:
    table_key: str
    table_name: str
    table_id: str | None
    anchors: list[TableAnchorCandidate]
    paired_columns: list[ColumnPair]
    column_drafts: list[ColumnSymbolicDraft]
    steward_notes: list[str]


@dataclass(slots=True)
class SymbolicGroundingDraft:
    table_drafts: dict[str, TableSymbolicDraft]
    fast_path_mask: dict[str, set[str]]
    rerank_packets: dict[str, list[RerankColumnPacket]]


class ColumnGroundingEngine:
    """Module 1A grounding engine."""

    ENGINE_VERSION: Final[str] = "2.0.0"

    def __init__(
        self, source: KnowledgeSource, *, calibration: CalibrationParams | None = None
    ) -> None:
        self._source = source
        self._calibration = (
            calibration or source.calibration_params or CalibrationParams.analytic_defaults()
        )

    def _aligns(self, u: str | None, t: str, ta: set[str], tp: set[str]) -> bool:
        if not u:
            return False
        if u == t or u in ta or u in tp:
            return True
        ua = set(self._source.get_ancestors(u, max_depth=8))
        return t in ua or bool(ua & tp)

    def _played(self, u: str | None) -> set[str]:
        if not u or (it := self._source.get_item(u)) is None:
            return set()
        out = set(it.neighborhood_links.get("is_played_by", ()))
        if it.semantic_context.played_by_uri:
            out.add(it.semantic_context.played_by_uri)
        return out

    @staticmethod
    def _is_fk(col: ColumnEvidence) -> bool:
        return bool(col.fk_ref) or col.column_name.strip().lower().startswith(
            sc.FOREIGN_ROLE_PREFIXES
        )

    @staticmethod
    def _merge(dst: dict[str, RecallHit], hits: Sequence[RecallHit]) -> None:
        for h in hits:
            if h.entry_id not in dst or h.raw_score > dst[h.entry_id].raw_score:
                dst[h.entry_id] = h

    def _recall_anchors(
        self, tbl: TableEvidence, sctx: sc.StewardTableContext, kinds: frozenset[str]
    ) -> list[TableAnchorCandidate]:
        rq, eq = sc.build_table_anchor_query(tbl, sctx, self._source)
        hits: dict[str, RecallHit] = {}
        for q in dict.fromkeys((rq, eq)):
            if q.strip():
                self._merge(hits, self._source.recall_by_text(q, entry_kinds=kinds, top_k=10))
        out: list[TableAnchorCandidate] = []
        for h in hits.values():
            it = self._source.get_item(h.entry_id)
            if it is None or (arch := it.semantic_context.archetype) not in _ANCHOR_ARCH:
                continue
            cf = float(h.raw_score)
            out.append(TableAnchorCandidate(
                entry_id=h.entry_id, label=it.label, archetype=arch, origin_kind=h.origin_kind,
                confidence=cf, replaces_uris=list(it.replaces_uris),
                suggested_predicate=(
                    "skos:exactMatch" if cf >= 0.85
                    else ("skos:closeMatch" if cf >= 0.55 else "skos:relatedMatch")
                ),
            ))
        out.sort(key=lambda a: (-a.confidence, a.entry_id))
        return out[:5]

    def _is_owner_bk(
        self, d: ColumnSymbolicDraft, tu: str | None, ta: set[str], tp: set[str]
    ) -> bool:
        c, p = d.column, d.profile
        sh = p.shape
        if (
            d.base_ctx.prescreen_category
            or sh.surrogate_kind in ("monotonic_int", "random_uuid_v4")
            or p.pii_tags or self._is_fk(c) or _SEQ_RE.search(c.column_name.strip())
        ):
            return False
        if c.is_pk:
            return True
        if sh.distinct_ratio < 0.90:
            return False
        if (ch := d.symbolic_outcome.choices) and ch[0].semantic_context.archetype == "identifier":
            u = ch[0].semantic_context.identified_entity_uri
            if u is None or tu is None or self._aligns(u, tu, ta, tp):
                return True
        return sh.surrogate_kind == "prefixed_operational_id" or any(
            sh.structural_patterns.get(k, 0.0) >= 0.90 for k in sc.NON_PII_BK_PATTERNS
        )

    def _reconcile_anchors(
        self, anchors: list[TableAnchorCandidate], drafts: Sequence[ColumnSymbolicDraft]
    ) -> list[TableAnchorCandidate]:
        act = [d for d in drafts if not d.base_ctx.prescreen_category]
        uok = any(
            not self._is_fk(d.column) and not d.profile.pii_tags
            and (d.column.is_pk or d.profile.shape.distinct_ratio >= 0.95)
            and d.symbolic_outcome.status in ("MATCHED", "AMBIGUOUS")
            for d in act
        )
        rec: list[TableAnchorCandidate] = []
        for a in anchors:
            aid = a.entry_id
            aset, play = set(self._source.get_ancestors(aid, max_depth=8)), self._played(aid)
            cols, probs, ok, fc = [], [], False, 0
            for d in act:
                if not (ch := d.symbolic_outcome.choices):
                    continue
                t = ch[0]
                s = t.semantic_context
                if t.confidence.point < 0.35 or not (
                    t.entry_id == aid or self._aligns(s.domain_entity_uri, aid, aset, play)
                    or self._aligns(s.identified_entity_uri, aid, aset, play)
                ):
                    continue
                c = d.column
                cols.append(c.column_name)
                probs.append(t.confidence.point)
                if self._is_fk(c):
                    fc += 1
                elif c.is_pk or d.profile.shape.distinct_ratio >= 0.95:
                    ok = True
            cf = a.confidence
            if n := len(probs):
                cf = max(0.0, min(1.0, 0.5 * cf + 0.5 * sum(probs) / n - 0.25 * fc / n))
            if cf >= 0.55 and (ok or uok):
                rec.append(a.model_copy(update={
                    "confidence": cf,
                    "suggested_predicate": "skos:exactMatch" if cf >= 0.85 else "skos:closeMatch",
                    "supporting_columns": cols,
                }))
        rec.sort(key=lambda x: (-x.confidence, x.entry_id))
        return rec

    def _assign_packet(self, d: ColumnSymbolicDraft) -> RerankColumnPacket:
        sl = d.symbolic_outcome.scored_candidates[:15]
        sent = {s.entry_id: i for i, s in enumerate(sl)}
        d.candidates = [
            replace(c, rerank_index=None if c.hard_veto else sent.get(c.entry_id, -1))
            for c in d.candidates
        ]
        cn, p = d.column.column_name, d.profile
        sums = []
        for i, s in enumerate(sl):
            c = s.source_input
            it = self._source.get_item(c.entry_id)
            raw = it.definition if it and it.definition else c.label
            sums.append(RerankCandidateSummary(
                index=i, label=c.label, entry_kind=c.entry_kind,
                definition=sc.strip_uris_and_pii(raw, col_name=cn, max_len=400) or c.label,
                semantic_hint=c.semantic_context.archetype, prior_score=round(s.probability, 6),
            ))
        sh = p.shape.model_copy(update={"observed_enum_values": []})
        return RerankColumnPacket(
            column_name=cn, data_type=d.column.data_type, steward_note=d.clean_steward_note,
            inferred_value_profile=p.model_copy(update={"shape": sh}),
            pii_tags=p.pii_tags, candidates=sums,
        )

    def _ground_table(
        self, tbl: TableEvidence, sctx: sc.StewardTableContext,
        anchors: list[TableAnchorCandidate], abn: Mapping[str, str],
        req: GroundingRequest, anc_on: bool,
    ) -> tuple[TableSymbolicDraft, set[str], list[RerankColumnPacket]]:
        src, cal, pol = self._source, self._calibration, req.policy
        tkey, tn, tcols = tbl.table_id or tbl.table_name, tbl.table_name, tbl.columns
        top = anchors[0] if anchors else None
        turi = top.entry_id if top else None
        tancs = set(src.get_ancestors(turi, max_depth=8)) if turi else set()
        tplay = self._played(turi)
        profs, unm, pre, fks = {}, {}, {}, {}
        for col in tcols:
            cn = col.column_name
            profs[cn], unm[cn] = sc.profile_column_evidence(col, tbl)
            pre[cn] = sc.check_etl_prescreen(col, tn)
            if col.fk_ref and (
                tgt := col.fk_ref.split("(", 1)[0].split(".", 1)[0].strip().lower()
            ) in abn:
                fks[cn] = abn[tgt]
        tctx = TableReasoningContext(
            table_name=tn, table_id=tbl.table_id, anchor_hypotheses=anchors,
            sibling_summaries=[f"{c.column_name}:{c.data_type}" for c in tcols[:50]],
            fk_targets=fks, steward_notes=list(sctx.cleaned_table_notes),
            paired_columns=sc.detect_paired_columns(
                tbl, profs, {}, {k: v[0] for k, v in pre.items()},
            ),
        )
        drafts: list[ColumnSymbolicDraft] = []
        for col in tcols:
            cn = col.column_name
            prof, (pc, pd) = profs[cn], pre[cn]
            isb = "BOOLEAN" in (prof.declared_logical_type, prof.inferred_logical_type)
            bq, etoks, cnote = sc.build_column_query(col, sctx, src)
            sh = prof.shape
            cands: list[CandidateChannelInputs] = []
            has_surf = chk = nochk = False
            if pc is None:
                th = {h.entry_id: h for h in src.recall_by_text(bq, top_k=25)}
                ct = sc.split_ident_tokens(cn)
                if etoks and set(etoks) != set(ct):
                    self._merge(
                        th, src.recall_by_text(" ".join(dict.fromkeys([*ct, *etoks])), top_k=15)
                    )
                sf = (
                    [] if (prof.pii_tags or sh.pii_suppressed)
                    else (sh.observed_enum_values or unm[cn][:50])
                )
                pats = sh.structural_patterns
                shits = src.match_values_or_mentions(
                    sf, pii_tags=prof.pii_tags, context_hint=sctx.context_text,
                    structural_patterns=pats,
                )
                mm = dict(th)
                self._merge(mm, shits)
                merged = sorted(mm.values(), key=lambda h: (-h.raw_score, h.entry_id))[:25]
                evl = src.evaluate_structured_candidates(col, prof, merged, table_context=tctx)
                chk = any(pats.get(k, 0.0) >= 0.50 for k in _CHK_PAT)
                nochk = any(pats.get(k, 0.0) >= 0.50 for k in _NOCHK_PAT)
                has_surf = chk or nochk or bool(shits)
                cands = sc.build_candidate_channel_inputs(
                    src, col, prof, merged, {k: float(v.raw_score) for k, v in th.items()},
                    {h.entry_id: float(h.raw_score) for h in shits}, {r.entry_id: r for r in evl},
                    bq, self._aligns, anc_on=anc_on, top_uri=turi, top_ancs=tancs,
                    top_played=tplay, has_chk=chk, has_nochk=nochk, is_bool=isb,
                )
            bc = ColumnCalibrationContext(
                cn, sh.profiled_non_null_count, prof.masked_count, True, has_surf, chk, nochk,
                anc_on, top is not None, None, False, False, pc, pd,  # type: ignore[arg-type]
            )
            out = select_and_calibrate(bc, cands, cal, pol)
            drafts.append(ColumnSymbolicDraft(
                tkey, tn, col, prof, bc, cands, out, pc is None and out.fast_path_eligible,
                cnote, isb,
            ))
        owners = {d.column.column_name for d in drafts if self._is_owner_bk(d, turi, tancs, tplay)}
        for d in drafts:
            cn, bc = d.column.column_name, d.base_ctx
            if (
                bc.prescreen_category is None
                and d.profile.shape.surrogate_kind in ("monotonic_int", "random_uuid_v4")
                and not self._is_fk(d.column) and not _SEQ_RE.search(cn.strip()) and (owners - {cn})
            ):
                d.base_ctx = replace(
                    bc, prescreen_category="technical_surrogate",
                    prescreen_detail="Surrogate with natural key.",
                )
                d.symbolic_outcome = select_and_calibrate(d.base_ctx, d.candidates, cal, pol)
                d.fast_path_eligible = False
        tmap: dict[str, str | None] = {}
        pmap: dict[str, str | None] = {}
        fps: set[str] = set()
        pkts: list[RerankColumnPacket] = []
        for d in drafts:
            cn, pc = d.column.column_name, d.base_ctx.prescreen_category
            if d.symbolic_outcome.choices:
                tmap[cn] = d.symbolic_outcome.choices[0].entry_id
            if pc is not None:
                pmap[cn] = pc
            elif d.fast_path_eligible:
                fps.add(cn)
            else:
                d.rerank_packet = self._assign_packet(d)
                pkts.append(d.rerank_packet)
        pairs = sc.detect_paired_columns(
            tbl, profs, tmap, pmap, include_sole_ccy=True, include_alt_keys=True,
        )
        ranc = self._reconcile_anchors(anchors, drafts) if anc_on else []
        return (
            TableSymbolicDraft(
                tkey, tn, tbl.table_id, ranc, pairs, drafts, list(sctx.cleaned_table_notes),
            ),
            fps, pkts,
        )

    def ground_symbolic(self, request: GroundingRequest) -> SymbolicGroundingDraft:
        """Run Stage 0-2 symbolic grounding."""
        kinds = self._source.metadata.anchor_entry_kinds
        anc_on = bool(kinds) and request.policy.enable_table_context
        ctxs, ancs, abn = {}, {}, {}
        rows = [(t.table_id or t.table_name, t) for t in request.tables]
        for k, tbl in rows:
            ctxs[k] = sc.extract_steward_table_context(tbl, request, self._source)
            ancs[k] = self._recall_anchors(tbl, ctxs[k], kinds) if anc_on else []
            if ancs[k]:
                abn[tbl.table_name.strip().lower()] = ancs[k][0].entry_id
        tds, fpm, rrp = {}, {}, {}
        for k, tbl in rows:
            tds[k], fpm[k], rrp[k] = self._ground_table(tbl, ctxs[k], ancs[k], abn, request, anc_on)
        return SymbolicGroundingDraft(tds, fpm, rrp)

    def finalize(
        self, request: GroundingRequest, draft: SymbolicGroundingDraft,
        rerank_results: Mapping[str, TableRerankResult] | None = None,
    ) -> GroundingResponse:
        """Finalize symbolic and optional rerank assignments."""
        meta, tres, modes, pol = self._source.metadata, [], set(), request.policy
        rr, det = rerank_results, pol.mode == "deterministic_only"
        rr_ok = rr is not None and pol.mode == "agentic"
        for tbl in request.tables:
            k, tn = tbl.table_id or tbl.table_name, tbl.table_name
            td, fps = draft.table_drafts[k], draft.fast_path_mask.get(k, set())
            tr = (rr.get(k) or rr.get(tn)) if rr is not None else None
            amap = {a.column_name: a for a in tr.assignments} if tr else {}
            cout = []
            for d in td.column_drafts:
                cn, bc, pkt = d.column.column_name, d.base_ctx, d.rerank_packet
                isf = cn in fps
                asgn = amap.get(cn) if rr_ok and not isf and not bc.prescreen_category else None
                if asgn is not None:
                    if (
                        not asgn.abstain and asgn.selected_index is not None and pkt
                        and asgn.selected_index >= len(pkt.candidates)
                    ):
                        raise OpenDVError(
                            f"Rerank index {asgn.selected_index} out of range for '{cn}'",
                            error_code=ErrorCode.AGENT_EXECUTION_FAILED,
                        )
                    fctx = replace(
                        bc, rerank_assignment=asgn, rerank_expected=True, is_fast_pathed=False
                    )
                    out = select_and_calibrate(fctx, d.candidates, self._calibration, pol)
                else:
                    out = d.symbolic_outcome
                em: GroundingExecutionMode = (
                    "deterministic_only" if det
                    else ("symbolic_fast_path" if isf
                          else ("agentic_reranked" if asgn is not None else "symbolic_only"))
                )
                modes.add(em)
                bp: str | None = None
                rej = out.best_rejected_candidate
                if (
                    out.status == "NO_MATCH"
                    and out.no_match_category == "out_of_vocabulary_domain" and rej
                ):
                    ancs = self._source.get_ancestors(rej.entry_id, max_depth=1)
                    bp = rej.semantic_context.domain_entity_uri or (ancs[0] if ancs else None)
                cout.append(ColumnGroundingResult(
                    table_name=tn, column_name=cn, status=out.status,
                    review_reason=out.review_reason, choices=out.choices,
                    no_match_probability=out.no_match_probability,
                    no_match_category=out.no_match_category, no_match_detail=out.no_match_detail,
                    suggested_broad_parent_uri=bp, best_rejected_candidate=rej,
                    steward_summary=out.steward_summary, inferred_value_profile=d.profile,
                    execution_mode_used=em, selection_rule_applied=out.selection_rule_applied,
                ))
            tres.append(TableGroundingResult(
                table_name=tn, table_id=tbl.table_id,
                inferred_table_anchors=td.anchors, paired_columns=td.paired_columns, columns=cout,
            ))
        rmode: GroundingExecutionMode = (
            "deterministic_only" if det
            else ("agentic_reranked" if "agentic_reranked" in modes
                  else ("symbolic_fast_path" if modes == {"symbolic_fast_path"}
                        else "symbolic_only"))
        )
        return GroundingResponse(
            knowledge_source_id=meta.source_id, knowledge_source_version=meta.version,
            knowledge_source_sha256=meta.sha256, engine_version=self.ENGINE_VERSION,
            mode_used=rmode, skip_reason=pol.skip_reason, tables=tres,
        )

    def ground(self, request: GroundingRequest) -> GroundingResponse:
        if request.policy.mode != "deterministic_only":
            raise OpenDVError(
                "ground() requires deterministic_only mode.",
                error_code=ErrorCode.AGENT_NOT_CONFIGURED,
            )
        return self.finalize(request, self.ground_symbolic(request), rerank_results=None)


__all__ = [
    "ColumnGroundingEngine", "ColumnSymbolicDraft", "SymbolicGroundingDraft", "TableSymbolicDraft",
]
