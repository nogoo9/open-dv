"""4-step deterministic projection from GroundingResponse into 36-op OntologyDelta list."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from pydantic import Field

from opendv.models import grounding as gm
from opendv.models import ontology as om
from opendv.models import ontology_deltas as od
from opendv.models.diagnostics import ClarificationQuestion, StrictModel
from opendv.models.events import GroundingReceipt
from opendv.models.project import EvolutionLockSpec
from opendv.models.sources import ColumnProfile, TableProfile
from opendv.service.workflows._grounding_orchestrator import (
    ProjectionState,
    emit_junction_relationships,
)
from opendv.service.workflows._grounding_reranker import (
    build_projected_attribute as _attr,
)
from opendv.service.workflows._grounding_reranker import (
    is_foreign_fk_column as _fk,
)
from opendv.service.workflows._grounding_reranker import (
    is_pii_column_result as _pii,
)
from opendv.service.workflows._grounding_reranker import (
    looks_like_enum_column as _is_enum,
)
from opendv.service.workflows._grounding_reranker import (
    pascal_identifier as _pascal,
)
from opendv.service.workflows._grounding_reranker import (
    resolve_key_authority as _auth,
)
from opendv.service.workflows._grounding_reranker import (
    slugify_identifier as _slug,
)
from opendv.service.workflows._grounding_reranker import (
    triage_phase_a_tables,
)
from opendv.service.workflows._ontology_grounding import GROUNDING_MIN_CONFIDENCE
from opendv.verifier._v6b_ontology_ap import SOURCE_PREFIX_RE

_ANC = frozenset({"entity", "agreement", "event_occurrence", "party_role"})
_CDK = frozenset({"seq_no", "line_no", "item_idx"})
_LBL = re.compile(r"(?i)(?:_name$|_label$|_desc$|_description$|_title$|^name$|^label$)")
_HIER = re.compile(r"(?i)^(?:parent_|child_|manager_|ultimate_)")


class GroundingProjectionResult(StrictModel):
    """Deterministic Step 4-5 projection outputs from a GroundingResponse."""

    suggested_deltas: list[od.OntologyDelta] = Field(default_factory=list)
    clarification_questions: list[ClarificationQuestion] = Field(default_factory=list)
    system_surrogate_cols: list[str] = Field(default_factory=list)
    system_metadata_cols: list[str] = Field(default_factory=list)
    held_table_ids: list[str] = Field(default_factory=list)


def _pick_key(
    cols: Sequence[tuple[gm.ColumnGroundingResult, ColumnProfile | None]],
    r8: set[str],
    r10: set[str],
) -> gm.ColumnGroundingResult | None:
    for c, cp in cols:
        if cp and cp.is_pk and c.column_name not in r10:
            return None if (c.column_name in r8 or _pii(c, cp)) else c
    for c, cp in cols:
        if (
            c.column_name not in r10
            and c.column_name not in r8
            and not _pii(c, cp)
            and not _fk(c.column_name, cp)
            and cp
            and cp.null_ratio <= 0.01
            and c.inferred_value_profile.shape.distinct_ratio >= 0.95
        ):
            return c
    return None


def _row4(
    st: ProjectionState, a_uri: str, c: gm.ColumnGroundingResult,
    cp: ColumnProfile | None, *, bk: bool,
) -> None:
    t = c.choices[0]
    su = t.entry_id if t.entry_kind in ("class", "enumeration", "individual_lifted") else None
    eu = (st.s2e.get(su) if su else None) or f"urn:opendv:enum:{_slug(su or t.label)}"
    obs = c.inferred_value_profile.shape.observed_enum_values
    st.put_enum(eu, _pascal(t.label), t.label, obs, su)
    if a_uri not in st.lhb:
        pii = _pii(c, cp)
        attr = _attr(c, cp, is_bk=bk, is_pii=pii, enum_uri=eu, std_prop_uri=t.entry_id)
        st.put_attr(a_uri, attr, pii=pii)


def _row6(
    st: ProjectionState, a_uri: str, tid: str, tn: str, c: gm.ColumnGroundingResult,
    cp: ColumnProfile | None, ub: str, unn: float,
) -> None:
    t, pii = c.choices[0], _pii(c, cp)
    sem, tgt = t.semantic_context, st.target_of(c, cp, tid)
    if tgt is None:
        st.put_attr(a_uri, _attr(c, cp, is_bk=False, is_pii=pii, std_prop_uri=t.entry_id), pii=pii)
        return
    if pii:
        st.tag(a_uri, "PII_ISOLATION")
    if tgt == a_uri and _HIER.search(c.column_name):
        tl = f"hal_{_slug(c.column_name)}"
    elif unn >= 0.95 and ub not in st.llk:
        tl = ub
    else:
        tl = f"{ub}_{_slug(c.column_name)}"
    st.add_rel(
        f"urn:opendv:{st.ns}:rel:{_slug(tid)}_{_slug(c.column_name)}",
        relationship_name=_pascal(c.column_name), source_concept_uri=a_uri, target_concept_uri=tgt,
        cardinality="MANY_TO_ONE", target_link=tl, grain_semantics=f"Per {tn}.{c.column_name}",
        description=f"Relationship via {c.column_name}",
        standard_property_uri=t.entry_id if t.entry_kind == "object_property" else None,
        role_uri=sem.role_uri, played_by_uri=sem.played_by_uri or None,
        source_table_id=tid, source_col_name=c.column_name,
    )


def project_grounding_to_deltas(
    response: gm.GroundingResponse,
    *,
    table_profiles: Sequence[TableProfile],
    existing_ontology: om.GraphAOntology,
    table_id_map: Mapping[str, str],
    receipts_by_id: Mapping[str, GroundingReceipt],
    evolution_locks: Sequence[EvolutionLockSpec] = (),
) -> GroundingProjectionResult:
    """Project GroundingResponse into OntologyDelta ops in 4 deterministic steps."""
    cps = {(t.table_id, c.col_name): c for t in table_profiles for c in t.columns}
    st = ProjectionState(
        existing_ontology, response, cps, table_id_map, receipts_by_id, evolution_locks,
    )
    tables: list[tuple[
        str, gm.TableGroundingResult, list[tuple[gm.ColumnGroundingResult, ColumnProfile | None]],
    ]] = []
    for t in response.tables:
        tid = (
            table_id_map.get(t.table_id or t.table_name)
            or t.table_id
            or table_id_map.get(t.table_name)
            or t.table_name
        )
        tables.append((tid, t, [(c, cps.get((tid, c.column_name))) for c in t.columns]))

    # Step 1: Phase A Triage
    r8m, r9m, pbm, r10s, r10m, qs, sur, met = triage_phase_a_tables(tables)

    # Step 2: Row 2 Table-Anchor Resolution & Structural Guards
    ap12: set[str] = set()
    juncs, mas, roles, hld = [], [], [], []
    for tid, tbl, ccps in tables:
        r8, r10 = r8m[tid], set(r10s.get(tid, [])) | set(r10m.get(tid, []))
        act = [(c, cp) for c, cp in ccps if c.column_name not in r10]
        pks = [(c, cp) for c, cp in act if cp and cp.is_pk]
        fks = [(c, cp) for c, cp in act if _fk(c.column_name, cp)]
        ct = SOURCE_PREFIX_RE.sub("", tbl.table_name)
        if (
            len(act) in (2, 3, 4)
            and len(pks) == 1
            and not fks
            and _is_enum(*pks[0])
            and any(_LBL.search(c.column_name) for c, _ in act if c != pks[0][0])
        ):
            ap12.add(tid)
            pkc = pks[0][0]
            su = pkc.choices[0].entry_id if pkc.choices else None
            eu = (st.s2e.get(su) if su else None) or f"urn:opendv:enum:{_slug(ct)}"
            obs = pkc.inferred_value_profile.shape.observed_enum_values
            st.put_enum(eu, _pascal(ct), ct, obs, su)
        elif (
            len(fks) == 1
            and len(nf := [x for x in pks if x not in fks]) == 1
            and (
                (kn := nf[0][0].column_name.lower()) in _CDK
                or kn.endswith(("_seq", "_line_no"))
            )
        ):
            mas.append((tbl, tid, ccps, nf[0][0].column_name))
        else:
            ok = _pick_key([x for x in act if x not in fks], r8, r10)
            if len(fks) >= 2 and ok is None and not ({c.column_name for c, _ in pks} & r8):
                juncs.append((tbl, tid, ccps))
            elif (
                (pks and all(c.column_name in r8 or _pii(c, cp) for c, cp in pks) and ok is None)
                or (ok is None and not tbl.inferred_table_anchors)
            ):
                hld.append(tid)
            else:
                anc = next(
                    (
                        a for a in tbl.inferred_table_anchors
                        if a.suggested_predicate in {"skos:exactMatch", "skos:closeMatch"}
                        and a.to_quantized_confidence() >= GROUNDING_MIN_CONFIDENCE
                    ),
                    None,
                )
                if anc and anc.archetype == "party_role":
                    roles.append((tbl, tid, ccps, anc))
                elif anc and anc.origin_kind == "project_graph_a" and anc.entry_id in st.cn:
                    st.tc[tid] = anc.entry_id
                    if ok:
                        st.bind(anc.entry_id, tid, ok.column_name)
                elif (
                    anc
                    and anc.origin_kind == "reference_standard"
                    and (em := st.ext_map(anc, tid))
                ):
                    kc = ok.column_name if ok else None
                    st.tc[tid] = st.fold_or_new(tid, _pascal(anc.label), kc, em)
                elif ok is not None:
                    st.tc[tid] = st.fold_or_new(tid, _pascal(ct), ok.column_name)
                else:
                    hld.append(tid)

    for tbl, tid, ccps, anc in roles:
        ok = _pick_key(ccps, r8m[tid], set(r10s.get(tid, [])) | set(r10m.get(tid, [])))
        kc = ok.column_name if ok else None
        pby = next(
            (
                c.choices[0].semantic_context.played_by_uri
                for c, _ in ccps
                if c.choices and c.choices[0].semantic_context.played_by_uri
            ),
            None,
        )
        if plr := ((st.s2u.get(pby) or st.pby.get(pby)) if pby else None):
            st.tc[tid] = plr
            if kc:
                st.bind(plr, tid, kc)
        else:
            em = st.ext_map(anc, tid)
            rnm = _pascal(anc.label if em else SOURCE_PREFIX_RE.sub("", tbl.table_name))
            st.tc[tid] = st.fold_or_new(tid, rnm, kc, em, pby)

    mas_cols: dict[str, tuple[str, str]] = {}
    for _tbl, tid, ccps, cdk in mas:
        fk = next(((c, cp) for c, cp in ccps if _fk(c.column_name, cp)), None)
        if fk and (par := st.target_of(fk[0], fk[1], tid)):
            st.tc[tid] = par
            st.bind(par, tid, fk[0].column_name)
            st.con(par, "mas_cdks", f"{tid}:{cdk}")
            mas_cols[tid] = (fk[0].column_name, cdk)

    emit_junction_relationships(st, juncs, r10s, r10m)

    # Step 3: Row 9 + Phase B
    for tid, tbl, _ccps in tables:
        if tid in ap12 or tid in hld or (a_uri := st.tc.get(tid)) is None:
            continue
        mas_fk, mas_cdk = mas_cols.get(tid, (None, None))
        for cn in r10s.get(tid, []):
            st.con(a_uri, "system_surrogate_cols", f"{tid}:{cn}")
        for cn in r10m.get(tid, []):
            st.con(a_uri, "system_metadata_cols", f"{tid}:{cn}")
        for p in tbl.paired_columns:
            if p.kind in ("amount_currency", "effective_interval") and len(p.columns) == 2:
                ck = (
                    "amount_currency_pairs" if p.kind == "amount_currency" else "effective_interval"
                )
                st.con(a_uri, ck, f"{tid}:{p.columns[0]}:{p.columns[1]}")
        alts = {cn for p in tbl.paired_columns if p.kind == "alternative_keys" for cn in p.columns}
        for c, cp in r9m[tid]:
            if c.column_name == mas_fk:
                continue
            pii = _pii(c, cp)
            is_bk = bool(
                cp and cp.is_pk and not pii and a_uri not in st.lhb and c.column_name != mas_cdk
            )
            st.put_attr(a_uri, _attr(c, cp, is_bk=is_bk, is_pii=pii), pii=pii)
            if c.suggested_broad_parent_uri:
                st.con(a_uri, "suggested_broad_parents", c.suggested_broad_parent_uri)

        pbc = [x for x in pbm[tid] if x[0].column_name != mas_fk]
        unn = max(0.0, 1.0 - sum(
            cp.null_ratio for c, cp in pbc if cp and (
                (c.choices[0].entry_kind == "object_property" and bool(
                    c.choices[0].semantic_context.target_entity_uri
                    or c.choices[0].semantic_context.played_by_uri
                    or cp.fk_ref
                ))
                or c.choices[0].semantic_context.archetype == "party_role"
                or (
                    c.choices[0].semantic_context.archetype in _ANC - {"party_role"}
                    and c.choices[0].entry_id != a_uri
                    and st.s2u.get(c.choices[0].entry_id) != a_uri
                )
            )
        ))
        ub, bk_done = f"lnk_{_slug(SOURCE_PREFIX_RE.sub('', tbl.table_name))}", False
        for c, cp in pbc:
            top, is_cdk = c.choices[0], c.column_name == mas_cdk
            sem, ek, pii = top.semantic_context, top.entry_kind, _pii(c, cp)
            arch = sem.archetype
            has_tgt = bool(sem.target_entity_uri or sem.played_by_uri or (cp and cp.fk_ref))
            if (
                top.origin_kind == "project_graph_a"
                and arch != "identifier"
                and ek not in ("object_property", "enumeration")
                and (
                    ek == "datatype_property"
                    or sem.domain_entity_uri == a_uri
                    or top.entry_id == a_uri
                )
            ):
                attr = _attr(c, cp, is_bk=False, is_pii=pii, std_prop_uri=top.entry_id)
                st.put_attr(a_uri, attr, pii=pii)
            elif arch == "identifier":
                if pii or is_cdk:
                    attr = _attr(c, cp, is_bk=False, is_pii=pii, std_prop_uri=top.entry_id)
                    st.put_attr(a_uri, attr, pii=pii)
                    continue
                ent_uri = sem.identified_entity_uri
                own = not _fk(c.column_name, cp, ent_uri, a_uri) and (
                    ent_uri == a_uri
                    or st.s2u.get(ent_uri or "") == a_uri
                    or (
                        ent_uri is None
                        and (cp is None or cp.fk_ref is None)
                        and (
                            bool(cp and cp.is_pk)
                            or c.inferred_value_profile.shape.distinct_ratio >= 0.95
                            or sem.domain_entity_uri == a_uri
                        )
                    )
                )
                if not own:
                    _row6(st, a_uri, tid, tbl.table_name, c, cp, ub, unn)
                elif a_uri in st.lhb:
                    if c.column_name in st.lhb[a_uri]:
                        st.bind(a_uri, tid, c.column_name)
                    else:
                        attr = _attr(c, cp, is_bk=False, is_pii=False, std_prop_uri=top.entry_id)
                        st.put_attr(a_uri, attr)
                        st.add_rel(
                            f"urn:opendv:{st.ns}:rel:sal_{_slug(tid)}_{_slug(c.column_name)}",
                            relationship_name=f"SameAs_{_pascal(c.column_name)}",
                            source_concept_uri=a_uri, target_concept_uri=a_uri,
                            cardinality="ONE_TO_ONE", target_link=f"sal_{_slug(st.cn[a_uri])}",
                            grain_semantics=f"Crosswalk {c.column_name}",
                            description=f"Same-as link for {c.column_name}",
                            source_table_id=tid, source_col_name=c.column_name,
                            target_col_name=next(iter(sorted(st.lhb[a_uri])), None),
                        )
                else:
                    alt = bk_done and c.column_name in alts and not (cp and cp.is_pk)
                    attr = _attr(c, cp, is_bk=not alt, is_pii=False, std_prop_uri=top.entry_id)
                    st.put_attr(a_uri, attr)
                    st.bind(a_uri, tid, c.column_name)
                    if not alt:
                        bk_done = True
                        tier, bkcc, anm = _auth(c.column_name, sem)
                        st.dl.append(od.SetConceptKeyAuthority(
                            concept_uri=a_uri, key_authority_tier=tier, bkcc_default=bkcc,
                        ))
                        st.dl.append(od.AddKeyAuthorityPattern(
                            concept_uri=a_uri,
                            pattern=om.KeyAuthorityPattern(
                                tier=tier, authority_name=anm,
                                column_patterns=[c.column_name], bkcc_policy=bkcc or "LOCAL",
                            ),
                        ))
            elif (
                arch == "code_classifier"
                or ek == "enumeration"
                or (ek == "individual_lifted" and arch not in _ANC)
            ):
                _row4(st, a_uri, c, cp, bk=bool(cp and cp.is_pk and not is_cdk))
            elif (
                ek == "datatype_property"
                or arch in ("value_object", "temporal", "quantity_measure")
                or sem.pii_category_hint is not None
                or (arch in _ANC and (top.entry_id == a_uri or st.s2u.get(top.entry_id) == a_uri))
                or (ek == "object_property" and not has_tgt)
            ):
                if _is_enum(c, cp):
                    _row4(st, a_uri, c, cp, bk=False)
                elif _HIER.search(c.column_name) and has_tgt and not is_cdk:
                    _row6(st, a_uri, tid, tbl.table_name, c, cp, ub, unn)
                else:
                    attr = _attr(c, cp, is_bk=False, is_pii=pii, std_prop_uri=top.entry_id)
                    st.put_attr(a_uri, attr, pii=pii)
            elif (
                ((ek == "object_property" and has_tgt) or arch in _ANC or (cp and cp.fk_ref))
                and not is_cdk
            ):
                _row6(st, a_uri, tid, tbl.table_name, c, cp, ub, unn)
            elif _is_enum(c, cp):
                _row4(st, a_uri, c, cp, bk=False)
            else:
                attr = _attr(c, cp, is_bk=False, is_pii=pii, std_prop_uri=top.entry_id)
                st.put_attr(a_uri, attr, pii=pii)

    # Step 4: Coalesced SetConceptGovernanceTags
    for u in sorted(st.chg):
        if u in st.cn:
            st.dl.append(od.SetConceptGovernanceTags(
                concept_uri=u,
                governance_tags=list(st.tgs.get(u, [])),
                governance_constraints=dict(st.cns.get(u, {})),
            ))
    return GroundingProjectionResult(
        suggested_deltas=st.dl, clarification_questions=qs,
        system_surrogate_cols=sur, system_metadata_cols=met, held_table_ids=hld,
    )


__all__ = ["GroundingProjectionResult", "project_grounding_to_deltas"]
