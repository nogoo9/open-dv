"""FIBO Pack v2 KnowledgeSource implementation (Layer-2a)."""

from __future__ import annotations

import json
import re
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from opendv.grounding.kernel import IsoCodesTable, load_iso_codes
from opendv.grounding.kernel.lexical import LexicalIndex
from opendv.grounding.kernel.pii_core import MASKED_PII_TOKEN, has_redacted_placeholder
from opendv.grounding.providers.fibo.acronyms import (
    FINANCIAL_ACRONYMS,
    check_arch_type_veto,
    expand_financial_tokens,
    quantize_recall_score,
    score_candidate_shape_range,
)
from opendv.grounding.providers.fibo.closure import SubclassClosureResolver
from opendv.grounding.providers.fibo.closure_rules import (
    BENCHMARK_LOCALS,
    CMNS_RANGE_ARCHETYPES,
    FIBO_ID_SPECS,
    FIBO_INDIVIDUAL_TYPE_PRIORITY,
    FIBO_PII_MAP,
    extract_individual_keys,
    infer_property_pii_fallback,
    infer_xsd_range_archetype,
    verify_expected_sha,
)
from opendv.grounding.providers.fibo.closure_rules import (
    uri_local_name as _loc,
)
from opendv.grounding.providers.fibo.loader import (
    FiboReferenceOntologyLoader,
    ReferenceOntologyCorruptError,
    get_default_fibo_loader,
)
from opendv.grounding.structured.validators import compute_structural_patterns
from opendv.models.grounding import (
    ArchetypeKind,
    ColumnEvidence,
    EntryKind,
    InferredValueProfile,
    SemanticContext,
)
from opendv.models.grounding_spi import (
    BackendReasoningResult,
    CalibrationParams,
    KnowledgeItem,
    KnowledgeSourceMetadata,
    RecallHit,
    TableReasoningContext,
)
from opendv.models.vault_ir import DecimalType

_ZERO: Final[Decimal] = Decimal("0.0000")
_TOK_RE = re.compile(r"[A-Za-z0-9_]+")


class FiboKnowledgeSource:
    """Synchronous FIBO Pack v2 KnowledgeSource implementing Layer-2a SPI."""

    def __init__(
        self, loader: FiboReferenceOntologyLoader | None = None, *,
        pack_path: Path | str | None = None, pack_bytes: bytes | None = None,
        expected_sha256: str | None = None, verify_integrity: bool = True,
        calibration_path: Path | str | None = None, config_path: Path | str | None = None,
    ) -> None:
        if loader is not None:
            if verify_integrity and not loader.integrity_verified:
                raise ReferenceOntologyCorruptError("Unverified FiboReferenceOntologyLoader")
            if expected_sha256 is not None:
                verify_expected_sha(expected_sha256, loader.pack_sha256)
            self._loader = loader
        elif pack_bytes is not None:
            self._loader = FiboReferenceOntologyLoader.from_bytes(
                pack_bytes, expected_sha256=expected_sha256, verify_integrity=verify_integrity
            )
        else:
            self._loader = get_default_fibo_loader(
                pack_path=pack_path, expected_sha256=expected_sha256,
                verify_integrity=verify_integrity,
            )
        self._cal = (
            Path(calibration_path) if calibration_path is not None
            else Path(__file__).with_name("fibo_calibration.json")
        )
        self._iso: IsoCodesTable = load_iso_codes()
        ldr = self._loader
        self._closure = SubclassClosureResolver(ldr)
        cls, props = ldr.get_classes(), ldr.get_properties()
        self._func: set[str] = {
            u for u, p in props.items()
            if not p.get("deprecated") and (
                p.get("is_functional") or p.get("is_inverse_functional")
            )
        } | {
            a["property_uri"] for c in cls.values() if not c.get("deprecated")
            for a in (c.get("associated_properties") or ())
            if a.get("max_card") == 1 and isinstance(a.get("property_uri"), str)
        }
        self._items: dict[str, KnowledgeItem] = {}
        self._pat: dict[str, str] = {}
        self._build_items(cls, props)
        self._ind: dict[str, tuple[str, str]] = {}
        self._build_inds(ldr.get_individuals())
        self._lex = LexicalIndex(config_path=config_path, acronyms=FINANCIAL_ACRONYMS)
        self._lex.index_items(
            list(self._items.values()),
            origin_source_id=ldr.pack_id, origin_kind="reference_standard",
        )

    def _pii(self, uris: Sequence[str]) -> str | None:
        ldr = self._loader
        for u in uris:
            vis, q = {u}, deque([(u, 0)])
            while q:
                curr, d = q.popleft()
                if (ln := _loc(curr)) == "InternationalBankAccountIdentifier":
                    return "IBAN"
                if ln in FIBO_PII_MAP:
                    return FIBO_PII_MAP[ln]
                if d < 12:
                    for pid in sorted(ldr.get_direct_superclasses(curr)):
                        if pid not in vis:
                            vis.add(pid)
                            q.append((pid, d + 1))
        return None

    def _rk(self, ru: str) -> tuple[str | None, bool]:
        if ru.startswith("http://www.w3.org/2001/XMLSchema#"):
            return infer_xsd_range_archetype(ru)
        it = self._items.get(ru)
        if it is not None:
            return (it.semantic_context.archetype, False)
        return (CMNS_RANGE_ARCHETYPES.get(_loc(ru)), False)

    def _build_items(
        self, cls: Mapping[str, Mapping[str, Any]], props: Mapping[str, Mapping[str, Any]]
    ) -> None:
        ldr, clo, iso, items = self._loader, self._closure, self._iso, self._items

        def _put(
            eid: str, lbl: str, ek: EntryKind, src: Mapping[str, Any], pkey: str,
            links: dict[str, list[str]], meta: dict[str, Any], **skw: Any,
        ) -> None:
            items[eid] = KnowledgeItem(
                entry_id=eid, label=lbl, entry_kind=ek, definition=str(src.get("definition", "")),
                aliases=[str(s) for s in src.get("synonyms", ())],
                parent_ids=[str(s) for s in src.get(pkey, ())],
                neighborhood_links=links, replaces_uris=ldr.get_replaces_uris(eid),
                semantic_context=SemanticContext(**skw), metadata=meta,
            )

        for uri, c in sorted(cls.items()):
            if c.get("deprecated"):
                continue
            loc, ra = _loc(uri), c.get("archetype")
            arch: ArchetypeKind | None = (
                "identifier" if loc == "FinancialInstrumentGlobalIdentifier"
                else (ra if isinstance(ra, str) else None)  # type: ignore[assignment]
            )
            pk, auth, ig = FIBO_ID_SPECS.get(loc, (None, None, False))
            if uri == iso.fibo_currency_uri:
                pk, auth = "iso_4217_ccy", "ISO_4217"
            elif uri == iso.fibo_country_uri:
                pk, auth = "iso_3166_country", "ISO_3166"
            if pk:
                self._pat[pk] = uri
            pl = clo.resolve_role_players(uri, reduce_leaves=True) if arch == "party_role" else []
            ids = clo.resolve_identified_entities(uri, reduce_leaves=True) or [
                str(u) for u in c.get("identified_entity_uris", ()) if u
            ]
            _put(
                uri, str(c.get("label", "")), "class", c, "sub_class_of",
                {"disjoint_with": sorted(ldr.get_disjoint_classes(uri)),
                 "is_played_by": pl, "identified_entity_uris": ids},
                {"module": c.get("module", ""), "has_surface_rule": bool(pk), "pattern_key": pk},
                archetype=arch, identified_entity_uri=ids[0] if len(ids) == 1 else None,
                played_by_uri=pl[0] if len(pl) == 1 else None,
                identifier_scope="global_standard" if ig else None,
                standard_authority=auth, pii_category_hint=self._pii([uri]),
            )
        if (cu := iso.fibo_country_uri) not in items:
            self._pat["iso_3166_country"] = cu
            items[cu] = KnowledgeItem(
                entry_id=cu, label="country", entry_kind="class",
                definition="Geopolitical sovereign country or territory (ISO 3166-1).",
                aliases=["country code", "iso country", "nation"],
                semantic_context=SemanticContext(
                    archetype="code_classifier", identifier_scope="global_standard",
                    standard_authority="ISO_3166",
                ),
                metadata={
                    "module": "CMNS", "has_surface_rule": True, "pattern_key": "iso_3166_country",
                },
            )
        for uri, p in sorted(props.items()):
            if p.get("deprecated"):
                continue
            pt = p.get("property_type")
            doms = sorted(set(p.get("domain", ())) | ldr.get_restriction_domains(uri))
            rngs = sorted(set(p.get("range", ())) | ldr.get_restriction_ranges(uri))
            pa: ArchetypeKind | None = None
            te = rf = pp = None
            ib = False
            for ru in rngs:
                rk, rb = self._rk(ru)
                ib = ib or rb
                if rk in ("temporal", "quantity_measure", "code_classifier",
                          "value_object", "identifier"):
                    pa = rk  # type: ignore[assignment]
                    break
                if pt == "ObjectProperty" and rk in (
                    "entity", "agreement", "event_occurrence", "party_role"
                ):
                    te = ru
                    if rk == "party_role":
                        pa, rf = "party_role", ru
                        if (ri := items.get(ru)) is not None:
                            pp = ri.semantic_context.played_by_uri
                    break
            pii = self._pii(rngs) or infer_property_pii_fallback(uri)
            _put(
                uri, str(p.get("label", "") or _loc(uri)),
                "object_property" if pt == "ObjectProperty" else "datatype_property",
                p, "sub_property_of",
                {"domain": doms, "range": rngs,
                 "inverse_of": sorted(ldr.get_inverse_properties(uri))},
                {"module": p.get("module", ""), "is_functional": uri in self._func,
                 "is_boolean_datatype": ib},
                archetype=pa, domain_entity_uri=doms[0] if doms else None,
                target_entity_uri=te, role_uri=rf, played_by_uri=pp, pii_category_hint=pii,
            )

    def _build_inds(self, inds: Mapping[str, Mapping[str, Any]]) -> None:
        ancs = self._loader.derived_indexes.ancestors_with_self
        for iu, ind in sorted(inds.items()):
            if ind.get("deprecated"):
                continue
            ts = [str(t) for t in ind.get("types", ()) if t]
            par = next(
                (t for pr in FIBO_INDIVIDUAL_TYPE_PRIORITY for t in ts
                 if t in self._items and any(_loc(a) == pr for a in ancs.get(t, {t}))),
                next((t for t in ts if t in self._items), None),
            )
            if par is None:
                continue
            plbl = self._items[par].label
            for rk in extract_individual_keys(iu, ind):
                if nk := rk.strip().casefold():
                    self._ind.setdefault(nk, (par, plbl))

    @property
    def metadata(self) -> KnowledgeSourceMetadata:
        return KnowledgeSourceMetadata(
            source_id=self._loader.pack_id, version=self._loader.version,
            sha256=self._loader.pack_sha256, anchor_entry_kinds=frozenset({"class"}),
            origin_kind="reference_standard",
        )

    @property
    def calibration_params(self) -> CalibrationParams | None:
        if not self._cal.is_file():
            return None
        raw = json.loads(self._cal.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return CalibrationParams.model_validate(raw)
        upd = {
            k: v for k, v in raw.items()
            if k not in ("schema_version", "split_seed", "heldout_metrics")
        }
        upd["calibration_mode"] = "empirical_calibrated"
        return CalibrationParams.model_validate(upd)

    def expand_query_terms(
        self, tokens: Sequence[str], *, context_text: str | None = None
    ) -> list[str]:
        ct = {t.lower() for t in _TOK_RE.findall(context_text)} if context_text else None
        return list(dict.fromkeys(
            t for t in expand_financial_tokens(tokens, context_tokens=ct) if t
        ))

    def recall_by_text(
        self, query: str, *, entry_kinds: AbstractSet[str] | None = None, top_k: int = 25
    ) -> list[RecallHit]:
        return self._lex.match(query, top_k=top_k, entry_kinds=entry_kinds)

    def match_values_or_mentions(
        self, surface_forms: Sequence[str], *, pii_tags: Sequence[str] = (),
        context_hint: str | None = None, structural_patterns: Mapping[str, float] | None = None,
    ) -> list[RecallHit]:
        _ = context_hint
        cln = [
            s for v in surface_forms if v is not None and (s := str(v).strip())
            and s != MASKED_PII_TOKEN and not has_redacted_placeholder(s)
        ]
        if structural_patterns is None:
            if pii_tags or not cln:
                return []
            pats: Mapping[str, float] = compute_structural_patterns(cln)
        else:
            pats = structural_patterns
        hits: dict[str, RecallHit] = {}

        def _add(eid: str, ek: EntryKind, r: float) -> None:
            if (it := self._items.get(eid)) is None:
                return
            sc = quantize_recall_score(r)
            if eid not in hits or sc > hits[eid].raw_score:
                hits[eid] = RecallHit(
                    entry_id=eid, label=it.label, entry_kind=ek, raw_score=sc,
                    origin_source_id=self._loader.pack_id, origin_kind="reference_standard",
                )

        for pk, uri in self._pat.items():
            if (r := pats.get(pk, 0.0)) >= 0.50:
                _add(uri, "class", r)
        if cln and not pii_tags:
            nc = float(len(cln))
            for names, uri in (
                (self._iso.currency_names, self._iso.fibo_currency_uri),
                (self._iso.country_names, self._iso.fibo_country_uri),
            ):
                ns = {n.casefold() for n in names.values()}
                if (r := sum(1 for v in cln if v.casefold() in ns) / nc) >= 0.50:
                    _add(uri, "class", r)
            for (pu, _), c in Counter(
                m for v in cln if (m := self._ind.get(v.casefold())) is not None
            ).items():
                if (r := c / nc) >= 0.50:
                    _add(pu, "individual_lifted", r)
        return sorted(hits.values(), key=lambda h: (-h.raw_score, h.entry_id))[:25]

    def get_item(self, entry_id: str) -> KnowledgeItem | None:
        return self._items.get(entry_id)

    def get_ancestors(self, entry_id: str, *, max_depth: int = 8) -> list[str]:
        if max_depth <= 0 or entry_id not in self._items:
            return []
        vis: set[str] = {entry_id}
        ancs: list[str] = []
        q: deque[tuple[str, int]] = deque([(entry_id, 0)])
        while q and len(ancs) < max_depth:
            curr, d = q.popleft()
            if (it := self._items.get(curr)) is None:
                continue
            for pid in it.parent_ids:
                if pid not in vis:
                    vis.add(pid)
                    ancs.append(pid)
                    if len(ancs) >= max_depth:
                        return ancs
                    if d + 1 < max_depth:
                        q.append((pid, d + 1))
        return ancs

    def _aligns(self, ua: str, ub: str) -> bool:
        am = self._loader.derived_indexes.ancestors_with_self
        if ua == ub or ub in am.get(ua, ()) or ua in am.get(ub, ()):
            return True
        ib = self._items.get(ub)
        return bool(
            ib and ib.semantic_context.archetype == "party_role"
            and any(self._aligns(ua, p) for p in ib.neighborhood_links.get("is_played_by", ()))
        )

    def evaluate_structured_candidates(
        self, column: ColumnEvidence, profile: InferredValueProfile,
        candidates: Sequence[RecallHit], *, table_context: TableReasoningContext | None = None,
    ) -> list[BackendReasoningResult]:
        dl, il, sh = profile.declared_logical_type, profile.inferred_logical_type, profile.shape
        pats = sh.structural_patterns
        t = (
            "DATE32" in (dl, il) or "TIMESTAMP_UTC_US" in (dl, il)
            or pats.get("iso_date", 0.0) >= 0.80 or pats.get("iso_timestamp", 0.0) >= 0.80
        )
        b = "BOOLEAN" in (dl, il)
        n = not (t or b) and (
            dl in ("INT32", "INT64") or isinstance(dl, DecimalType)
            or il in ("INT32", "INT64") or isinstance(il, DecimalType)
        )
        f = isinstance(dl, DecimalType) or isinstance(il, DecimalType)
        cat = "UTF8_STRING" in (dl, il) and not (t or b or f)
        uniq = column.is_pk or sh.distinct_ratio >= 0.95
        top_a = (
            table_context.anchor_hypotheses[0].entry_id
            if (table_context and table_context.anchor_hypotheses) else None
        )
        fk = table_context.fk_targets.get(column.column_name) if table_context else None
        am, res = self._loader.derived_indexes.ancestors_with_self, []
        for hit in candidates:
            hid = hit.entry_id
            if (item := self._items.get(hid)) is None:
                res.append(BackendReasoningResult(
                    entry_id=hid, shape_range_score=-1.0, hard_veto=True,
                    veto_reason="deprecated_or_unknown",
                ))
                continue
            sem, veto, inv, sr, notes = item.semantic_context, None, 0.0, 0.2, []
            if hit.entry_kind == "individual_lifted":
                if not cat:
                    veto = "individual_lifted_non_categorical"
                elif any(_loc(u) in BENCHMARK_LOCALS for u in am.get(hid, {hid})):
                    sem = sem.model_copy(update={"archetype": "code_classifier"})
                    sr, notes = 0.9, ["categorical_benchmark_individual"]
                else:
                    sr, notes = 0.85, ["categorical_lifted_individual"]
            else:
                pb, ta = bool(item.metadata.get("is_boolean_datatype")), sem.archetype
                veto = check_arch_type_veto(
                    ta, pb, is_num=n, is_temp=t, is_bool=b, is_float=f
                )
                if veto is None:
                    sr = score_candidate_shape_range(
                        ta, pb, item.metadata.get("pattern_key"), pats,
                        is_temp=t, is_bool=b, is_num=n, is_float=f, is_cat=cat, is_uniq=uniq,
                        decl=dl, look_enum=sh.looks_like_enum, dist_ratio=sh.distinct_ratio,
                        num_min=sh.numeric_min,
                    )
                    if item.entry_kind in ("object_property", "datatype_property"):
                        if item.metadata.get("is_functional") and uniq:
                            sr = min(1.0, sr + 0.25)
                            notes.append("functional_key_signal")
                        lk = item.neighborhood_links
                        doms, rngs = lk.get("domain", ()), lk.get("range", ())
                        hi = bool(lk.get("inverse_of"))
                        if top_a is not None:
                            md = [d for d in doms if self._aligns(d, top_a)]
                            if md:
                                sem = sem.model_copy(update={"domain_entity_uri": md[0]})
                            ra = any(self._aligns(r, top_a) for r in rngs)
                            fkr = bool(fk and any(self._aligns(r, fk) for r in rngs))
                            fkd = bool(fk and any(self._aligns(d, fk) for d in doms))
                            inv = (
                                0.3 if (md and (hi or fkr))
                                else (-0.4 if (not md and ra and (hi or fkd or doms)) else 0.0)
                            )
                            if any(self._loader.are_disjoint(d, top_a) for d in doms):
                                notes.append("disjoint_with_anchor")
            res.append(BackendReasoningResult(
                entry_id=hid, shape_range_score=-1.0 if veto else sr,
                hard_veto=veto is not None, veto_reason=veto, inverse_direction_score=inv,
                semantic_context=sem, support_detail=",".join(notes),
            ))
        return res

    def score(self, query: str, entry_id: str) -> Decimal:
        return _ZERO if entry_id not in self._items else self._lex.score(query, entry_id)


__all__ = ["FiboKnowledgeSource"]
