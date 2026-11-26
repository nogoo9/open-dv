"""Deterministic zero-LLM data-driven modeling hint survey (P1-P4)."""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import networkx as nx  # type: ignore[import-untyped]

from opendv.models.diagnostics import ClarificationQuestion
from opendv.models.modeling_preferences import SURROGATE_BK_RE, PkContainmentOverlap
from opendv.models.ontology import GraphAOntology
from opendv.models.pii_tokenizer import (
    MASKED_PII_TOKEN,
    PII_TOKEN_RE,
    detect_pii_in_column_name,
    detect_pii_in_value,
)
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
from opendv.models.vault_ir import DraftVaultIR

_AUDIT_RE = re.compile(
    r"(?i)^(?:id|pk|seq_id|row_id|uuid|surrogate_id|auto_id|load_dts|etl_snapshot_ts|"
    r"ingested_at|rec_src|batch_id|is_deleted|"
    r"(?:created|updated|modified|deleted)_(?:at|dts|date|by|ts))$"
)
_ACTOR_RE = re.compile(r"(?i)^(?:created|updated|modified|deleted)_by(?:_id|_user|_key)?$")
_MUT_RE = re.compile(
    r"(?i)^(?:(?:updated|modified)_(?:at|dts)|changed_at|"
    r"(?:effective|valid|eff)_(?:from|to)|applied_dts|as_of_(?:date|dts)|"
    r"status|state|is_active|active_flag)$"
)
_ESFX = ("_code", "_status", "_type", "_tier", "_grade", "_category")
_PREF_RE = re.compile(r"(?i)^(?:erp|crm|src|raw|stg|ods)_")
_SFX = ("_id", "_key", "_code")


@dataclass(frozen=True)
class _Ctx:
    t: dict[str, TableProfile]
    s: dict[str, str]
    c: dict[str, dict[str, ColumnProfile]]
    st: dict[str, str]
    n: dict[str, str | None]
    ov: list[PkContainmentOverlap]
    r: Mapping[str, Sequence[Mapping[str, Any]]]
    tr: Mapping[str, Mapping[str, float]]
    o: GraphAOntology | None
    d: DraftVaultIR | None


def _stem(name: str, sid: str = "") -> str:
    raw = name.split(".")[-1].strip().lower()
    if sid:
        raw = raw.removeprefix(f"{sid.strip().lower()}_")
    s = _PREF_RE.sub("", raw)
    if len(s) > 4 and s.endswith("ies"):
        return s[:-3] + "y"
    if len(s) > 3 and s.endswith("s") and not s.endswith(("ss", "us", "is", "as")):
        return s[:-1]
    return s


def _hid(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def _surr(col: ColumnProfile | None, name: str) -> bool:
    return bool(SURROGATE_BK_RE.match(name)) or (col is not None and col.surrogate_score >= 0.75)


def _safe(val: Any, pii: bool, name: str) -> str:
    if val is None:
        return "<NULL>"
    t = str(val)
    if pii or t == MASKED_PII_TOKEN or PII_TOKEN_RE.fullmatch(t):
        return "<NON_NULL>"
    return "<NON_NULL>" if detect_pii_in_value(t, col_name=name) else t[:64]


def _q(
    qid: str, concept: str, title: str, prompt: str, samples: list[str], impact: str, rec: str
) -> ClarificationQuestion:
    return ClarificationQuestion(
        question_id=qid, concept_name=concept, title=title, prompt=prompt,
        sample_rows=samples, impact_if_locked=impact, recommended_answer=rec,
    )


def _p1(ctx: _Ctx) -> list[ClarificationQuestion]:
    groups: list[tuple[str, list[str], list[str]]] = []
    seen: set[frozenset[str]] = set()
    if ctx.o is not None:
        for con in ctx.o.concepts:
            tids = sorted({tb.table_id for tb in con.table_bindings if tb.table_id in ctx.t})
            fs = frozenset(tids)
            if len(tids) >= 2 and fs not in seen:
                seen.add(fs)
                groups.append(
                    (_stem(con.concept_name), tids, [b.col_name for b in con.table_bindings])
                )
    if not groups and ctx.ov:
        ug: nx.Graph = nx.Graph()
        ovc: set[str] = set()
        for ov in ctx.ov:
            l_id, r_id = ov.left_table_id, ov.right_table_id
            cl, cr = ctx.c[l_id].get(ov.left_col), ctx.c[r_id].get(ov.right_col)
            if ctx.st[l_id] == ctx.st[r_id] or (cl and cl.is_pk and cr and cr.is_pk):
                ug.add_edge(l_id, r_id)
                ovc.update((ov.left_col, ov.right_col))
        ov_list = sorted(ovc)
        for cs in nx.connected_components(ug):
            comp = sorted(cs)
            fs = frozenset(comp)
            if len(comp) >= 2 and fs not in seen:
                seen.add(fs)
                groups.append((ctx.st[comp[0]], comp, ov_list))
    by_st: dict[str, list[str]] = defaultdict(list)
    for tid in sorted(ctx.t):
        by_st[ctx.st[tid]].append(tid)
    for st, tids in sorted(by_st.items()):
        fs = frozenset(tids)
        if len(tids) >= 2 and not any(fs <= s for s in seen):
            seen.add(fs)
            groups.append((st, tids, []))
    done = {
        sb.table_id for h in (ctx.d.hubs if ctx.d else ())
        if len(h.survivorship_priority) >= 2 for sb in h.source_bindings
    }
    out: list[ClarificationQuestion] = []
    for st, tids, ghints in groups:
        if any(t in done for t in tids):
            continue
        kset: set[str] = set(ghints)
        by_col: dict[str, list[tuple[str, ColumnProfile]]] = defaultdict(list)
        for tid in tids:
            for col in ctx.t[tid].columns:
                if col.is_pk or col.col_name == ctx.n[tid]:
                    kset.add(col.col_name)
                by_col[col.col_name].append((tid, col))
        shared = sorted(
            cn for cn, ents in by_col.items()
            if len({t for t, _ in ents}) >= 2 and cn not in kset
            and not _AUDIT_RE.match(cn) and not any(_surr(c, cn) for _, c in ents)
        )
        if not shared:
            continue
        a, b = tids[0], tids[1]
        m0, m1 = ctx.c[a], ctx.c[b]
        both = [c for c in m0 if c in m1]
        cands = (
            *ghints, ctx.n[a], ctx.n[b],
            *(c for c in both if m0[c].is_pk or m1[c].is_pk),
            *(c for c in both if c.lower().endswith(_SFX) and not _surr(m0[c], c)), *both,
        )
        jk = next((k for k in cands if k and k in m0 and k in m1), None)
        samples: list[str] = []
        if jk and (ra := ctx.r.get(a)) and (rb := ctx.r.get(b)):
            i0 = {str(r[jk]): r for r in ra if r.get(jk) is not None}
            i1 = {str(r[jk]): r for r in rb if r.get(jk) is not None}
            pii_c = {
                cn for cn in shared
                if detect_pii_in_column_name(cn) or any(x.pii_tags for _, x in by_col[cn])
            }
            for kv in sorted(set(i0) & set(i1)):
                for cn in shared:
                    v0, v1 = i0[kv].get(cn), i1[kv].get(cn)
                    if v0 != v1 and len(samples) < 5:
                        sv0, sv1 = _safe(v0, cn in pii_c, cn), _safe(v1, cn in pii_c, cn)
                        samples.append(f"{jk}={kv} {cn}: {ctx.s[a]}={sv0} vs {ctx.s[b]}={sv1}")
        sl = ", ".join(sorted({ctx.s[t] for t in tids}))
        out.append(_q(
            f"Q-P1-{st}-{_hid('|'.join(tids))}", f"hub_{st}", f"Survivorship: {st} ({sl})",
            f"{sl} overlap on [{', '.join(shared)}] for {st}.",
            samples, "survivorship_priority", sl,
        ))
    return out


def _fk_graph(ctx: _Ctx) -> nx.DiGraph:
    g: nx.DiGraph = nx.DiGraph()
    g.add_nodes_from(sorted(ctx.t))
    multi: set[str] = set()
    if ctx.o is not None:
        rels = ctx.o.relationships
        cnts = Counter((r.source_concept_uri, r.target_concept_uri) for r in rels)
        multi = {
            r.relationship_name.lower() for r in rels
            if cnts[(r.source_concept_uri, r.target_concept_uri)] > 1
        }
    tidx = {t.table_name.lower(): tid for tid, t in ctx.t.items()}

    def _ok(n: str) -> bool:
        return not (_ACTOR_RE.match(n.lower()) and n.lower() not in multi)

    pk_map: dict[str, set[str]] = defaultdict(set)
    for tid, tbl in sorted(ctx.t.items()):
        for c in tbl.columns:
            if c.fk_ref and _ok(c.col_name):
                ref = c.fk_ref.rsplit(".", 1)[0].strip()
                tgt = ref if ref in ctx.t else tidx.get(ref.lower())
                if tgt and tgt != tid:
                    g.add_edge(tid, tgt, fk_col=c.col_name)
            if c.is_pk:
                pk_map[c.col_name.lower()].add(tid)
        pk_map[f"{ctx.st[tid]}_id"].add(tid)
    for ov in ctx.ov:
        l_id, r_id = ov.left_table_id, ov.right_table_id
        ls, rs = ctx.st[l_id], ctx.st[r_id]
        if ls == rs:
            continue
        cl, cr = ctx.c[l_id].get(ov.left_col), ctx.c[r_id].get(ov.right_col)
        lp, rp = bool(cl and cl.is_pk), bool(cr and cr.is_pk)
        rev = (lp and not rp) or (ov.left_col == ctx.n[l_id] and ov.right_col != ctx.n[r_id]) or (
            ls in ov.right_col.lower() and rs not in ov.left_col.lower()
        )
        ch, pa, fk = (r_id, l_id, ov.right_col) if (rev and not (rp and not lp)) else (
            l_id, r_id, ov.left_col
        )
        if _ok(fk):
            g.add_edge(ch, pa, fk_col=fk)
    for tid, tbl in sorted(ctx.t.items()):
        for c in tbl.columns:
            low = c.col_name.lower()
            if not c.is_pk and not _AUDIT_RE.match(low) and _ok(c.col_name):
                for ptid in pk_map.get(low, ()):
                    if ptid != tid and ctx.st[ptid] != ctx.st[tid]:
                        g.add_edge(tid, ptid, fk_col=c.col_name)
    return g


def _p2(ctx: _Ctx, g: nx.DiGraph) -> list[ClarificationQuestion]:
    resolved = {_stem(b.root_hub.removeprefix("hub_")) for b in (ctx.d.bridges if ctx.d else ())}
    nodes, out = sorted(g.nodes()), []
    for root in nodes:
        if ctx.st[root] in resolved:
            continue
        paths: list[list[str]] = []
        tgts = [n for n in nodes if n != root]
        for p in nx.all_simple_paths(g, source=root, target=tgts, cutoff=4):
            if len(p) >= 3:
                paths.append(list(p))
                if len(paths) >= 25:
                    break
        for path in sorted(paths, key=lambda p: (-len(p), p)):
            stems = [ctx.st[n] for n in path]
            ch = " -> ".join(stems)
            out.append(_q(
                f"Q-P2-{stems[0]}-{stems[-1]}-{_hid('->'.join(path))}", f"brg_{'_'.join(stems)}",
                f"Bridge ({len(path) - 1} hops): {ch}", f"Materialize {ch} as Z3 brg_*?",
                [ch], "Z3 DraftBridgeSpec", f"Create brg_{'_'.join(stems)}",
            ))
    return out


def _p3(ctx: _Ctx, g: nx.DiGraph) -> list[ClarificationQuestion]:
    sats = ctx.d.satellites if ctx.d else ()
    esats = {s.parent_name for s in sats if s.sat_kind == "ESAT"}
    done = {
        b.table_id for lnk in (ctx.d.links if ctx.d else ())
        if lnk.link_kind == "TRANSACTIONAL" or lnk.link_name in esats for b in lnk.source_bindings
    }
    out: list[ClarificationQuestion] = []
    for tid, tbl in sorted(ctx.t.items()):
        fks = sorted(
            {d["fk_col"] for _, _, d in g.out_edges(tid, data=True) if "fk_col" in d}
            | {c.col_name for c in tbl.columns if c.fk_ref}
        )
        if tid in done or not fks:
            continue
        has_turn = any(float(v) > 0.0 for v in ctx.tr.get(tid, {}).values())
        mut = [c.col_name for c in tbl.columns if _MUT_RE.match(c.col_name)]
        reass: dict[str, list[str]] = {}
        rows = ctx.r.get(tid, ())
        if rows:
            nf = [c for c in tbl.columns if c.col_name not in fks]
            nat = ctx.n[tid]
            oc = next((c.col_name for c in nf if c.is_pk), None) or (
                nat if nat and nat not in fks else next((
                    c.col_name for c in nf
                    if not _AUDIT_RE.match(c.col_name) and c.col_name.lower().endswith(_SFX)
                ), None)
            )
            for fk in fks if oc else ():
                by_o: dict[str, set[str]] = defaultdict(set)
                for r in rows:
                    if oc and r.get(oc) is not None and r.get(fk) is not None:
                        by_o[str(r[oc])].add(str(r[fk]))
                hits = [f"{oc}={k}->{sorted(v)}" for k, v in sorted(by_o.items()) if len(v) > 1]
                if hits:
                    reass[fk] = hits[:3]
        if not (has_turn or mut or reass):
            continue
        st, foc = ctx.st[tid], next(iter(reass), fks[0])
        ev = [
            *reass.get(foc, ()),
            *([f"cols: {', '.join(mut)}"] if mut else ()),
            *(("fk_turnover > 0",) if has_turn else ()),
        ]
        out.append(_q(
            f"Q-P3-{st}-{foc}-{_hid(tid)}", f"lnk_{st}", f"Mutability: {tbl.table_name} ({foc})",
            f"{tbl.table_name} ({'; '.join(ev)}). Z2 esat_* or t_lnk_*?",
            ev[:5], "Z2 esat_* vs t_lnk_*", "Attach Z2 esat_*",
        ))
    return out


def _p4(ctx: _Ctx) -> list[ClarificationQuestion]:
    refs = {
        (e.table_id, e.col_name) for e in (ctx.d.column_triage if ctx.d else ())
        if e.disposition == "OMITTED_INTENTIONALLY" or (e.target_ref or "").startswith("ref_")
    }
    bks = {
        (bk.table_id, bk.col_name)
        for h in (ctx.d.hubs if ctx.d else ()) for bk in h.business_keys
    }
    out: list[ClarificationQuestion] = []
    for tid, tbl in sorted(ctx.t.items()):
        st, pks, nat = ctx.st[tid], [c for c in tbl.columns if c.is_pk], ctx.n[tid]
        if len(pks) == 1 and _surr(pks[0], pks[0].col_name) and nat and (tid, nat) not in bks:
            pkc = pks[0].col_name
            out.append(_q(
                f"Q-P4-SURR-{st}-{pkc}-{_hid(tid)}", f"hub_{st}",
                f"Surrogate {pkc} vs {nat} ({tbl.table_name})",
                f"{tbl.table_name}: use {nat} over {pkc}?",
                [], "surrogate_key_strategy", f"Use {nat}",
            ))
        for c in tbl.columns:
            cn = c.col_name
            skip = (
                (tid, cn) in refs or c.is_pk or c.fk_ref is not None or c.pii_tags
                or detect_pii_in_column_name(cn) or _AUDIT_RE.match(cn) or _surr(c, cn)
                or c.sql_type.strip().upper() in ("BOOLEAN", "BOOL")
            )
            if not skip and 2 <= c.distinct_count_est <= 20 and (
                tbl.row_count_est > c.distinct_count_est
                or cn.lower().endswith(_ESFX) or c.format_shape == "CODE_UPPER"
            ):
                out.append(_q(
                    f"Q-P4-ENUM-{st}-{cn}-{_hid(tid)}", f"ref_{cn}",
                    f"Low-card {tbl.table_name}.{cn} ({c.distinct_count_est})",
                    f"Promote {tbl.table_name}.{cn} to Z0 ref_* or Sat?",
                    [], "low_cardinality_strategy", "keep_in_satellite",
                ))
    return out


def survey_modeling_hints(
    profiles: Sequence[SourceProfile],
    ontology: GraphAOntology | None = None,
    source_rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    draft_ir: DraftVaultIR | None = None,
    answered_question_ids: Collection[str] | None = None,
    *,
    fk_turnover_by_table: Mapping[str, Mapping[str, float]] | None = None,
    max_questions: int | None = 5,
    offset: int = 0,
) -> tuple[list[ClarificationQuestion], dict[str, int]]:
    """Deterministically survey P1-P4 ambiguities and return paginated questions."""
    tbls = {t.table_id: t for p in profiles for t in p.tables}
    srcs = {t.table_id: p.source_id for p in profiles for t in p.tables}
    cols = {tid: {c.col_name: c for c in t.columns} for tid, t in tbls.items()}
    stems = {tid: _stem(t.table_name, srcs[tid]) for tid, t in tbls.items()}
    nats = {tid: t.best_non_surrogate_bk_candidate_col for tid, t in tbls.items()}
    ovs = [
        ov for p in profiles for ov in p.pk_containment_overlaps
        if ov.overlap_ratio >= 0.50 and ov.left_table_id != ov.right_table_id
        and ov.left_table_id in tbls and ov.right_table_id in tbls
    ]
    ctx = _Ctx(
        tbls, srcs, cols, stems, nats, ovs,
        source_rows_by_table or {}, fk_turnover_by_table or {}, ontology, draft_ir,
    )
    ans, fg = set(answered_question_ids or ()), _fk_graph(ctx)
    p1, p2, p3, p4 = (
        sorted((q for q in qs if q.question_id not in ans), key=lambda q: q.question_id)
        for qs in (_p1(ctx), _p2(ctx, fg), _p3(ctx, fg), _p4(ctx))
    )
    cands = [*p1, *p2, *p3, *p4]
    total, off = len(cands), max(0, offset)
    ret = cands[off:] if not max_questions else cands[off : off + max_questions]
    return ret, {
        "total_candidates": total, "returned_count": len(ret), "offset": off,
        "remaining_count": max(0, total - off - len(ret)),
        "P1": len(p1), "P2": len(p2), "P3": len(p3), "P4": len(p4),
    }


__all__ = ["survey_modeling_hints"]
