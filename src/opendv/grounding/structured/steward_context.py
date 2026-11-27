"""PII-safe steward context, profiling, pairing & candidate assembly (Layer-2a)."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from opendv.grounding.kernel.calibration import (
    CandidateChannelInputs,
    normalize_column_tokens,
    normalize_property_label,
)
from opendv.grounding.kernel.lexical import STOPWORDS
from opendv.grounding.kernel.pii_core import (
    MASKED_PII_TOKEN,
    detect_pii_in_column_name,
    detect_pii_in_value,
    ensure_no_redacted_placeholder,
    has_redacted_placeholder,
    strip_pii_spans,
)
from opendv.grounding.spi import KnowledgeSource
from opendv.grounding.structured.profiler import compute_column_shape_stats
from opendv.models.grounding import (
    ColumnEvidence,
    ColumnPair,
    GroundingRequest,
    InferredValueProfile,
    TableEvidence,
)
from opendv.models.grounding_spi import BackendReasoningResult, RecallHit
from opendv.models.vault_ir import DecimalType, map_sql_to_portable_type

_SEC_TRIGGERS: Final[frozenset[str]] = frozenset({
    "filing", "cik", "13f", "10k", "edgar", "regulator",
})
_SEC_EXCLUSIONS: Final[frozenset[str]] = frozenset({
    "second", "seconds", "sector", "security_id",
})
_SEC_INJECTED: Final[frozenset[str]] = frozenset({"securities", "exchange", "commission"})
_KEEP_TEMP: Final[frozenset[str]] = frozenset({
    "created_at", "updated_at", "applied_dts", "effective_from", "effective_to", "as_of_date",
})
_AUDIT_COLS: Final[frozenset[str]] = frozenset({
    "load_end_dts", "end_dts", "is_current", "is_current_flag", "load_dts", "rec_src",
    "record_source", "hash_diff", "row_hash", "etl_load_dts", "etl_run_id", "etl_run_digest",
    "etl_batch_id", "etl_snapshot_ts", "source_row_etag", "ingestion_ts", "ingested_at",
    "loaded_at",
})
FOREIGN_ROLE_PREFIXES: Final[tuple[str, ...]] = (
    "custodian_", "counterparty_", "issuer_", "advisor_", "broker_", "auditor_",
    "parent_", "ultimate_", "manager_", "executing_", "prime_",
)
NON_PII_BK_PATTERNS: Final[tuple[str, ...]] = (
    "iso_17442_lei", "iso_6166_isin", "cusip", "sedol", "figi", "swift_bic",
)
_AMT_TOKS: Final[frozenset[str]] = frozenset({
    "amt", "amount", "price", "nav", "value", "balance", "fee", "margin", "exposure",
    "accrual", "cash", "pledge", "principal", "notional",
})
_NON_CCY_TOKS: Final[frozenset[str]] = frozenset({
    "pct", "bps", "ratio", "qty", "shares", "count", "err", "var", "turnover", "rate",
})
_CCY_TOKS: Final[frozenset[str]] = frozenset({"ccy", "currency", "curr"})
_INTERVALS: Final[tuple[tuple[str, str], ...]] = (
    ("effective_from", "effective_to"), ("effective_start_date", "effective_end_date"),
    ("valid_from", "valid_to"), ("valid_from_dts", "valid_to_dts"),
    ("start_date", "end_date"), ("from_date", "to_date"), ("open_date", "close_date"),
)
_RAW_ID_RE = re.compile(r"[A-Za-z0-9_]+")
_CAMEL_1 = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_2 = re.compile(r"([A-Z]+)([A-Z][a-z])")
_URI_RE = re.compile(r"(?:https?://|urn:)[^\s,;)\]>\"']+")
_SRC_PREF_RE = re.compile(r"(?i)^(?:hub_)?(?:sfdc|sap|erp|crm|csv|parquet|tbl)_")


@dataclass(frozen=True, slots=True)
class StewardTableContext:
    """Sanitized table-, global-, and overlay-level stewardship context."""

    table_key: str
    table_name: str
    cleaned_table_notes: tuple[str, ...]
    cleaned_global_notes: tuple[str, ...]
    cleaned_overlay_notes: tuple[str, ...]
    column_notes: dict[str, str | None]
    raw_context_tokens: frozenset[str]
    sec_expansion_allowed: bool
    context_text: str


def split_ident_tokens(text: str) -> list[str]:
    if not text:
        return []
    s = _CAMEL_2.sub(r"\1 \2", _CAMEL_1.sub(r"\1 \2", text.strip()))
    return [t.lower() for t in re.findall(r"[A-Za-z0-9]+", s)]


def _singularize(tok: str) -> str:
    if len(tok) > 4 and tok.endswith("ies"):
        return f"{tok[:-3]}y"
    if len(tok) > 4 and tok.endswith(("ches", "shes", "xes", "zes", "sses")):
        return tok[:-2]
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith(("ss", "us", "is")):
        return tok[:-1]
    return tok


def _sanitize(text: str, *, col_name: str | None, max_len: int, strip_uris: bool, ctx: str) -> str:
    src = _URI_RE.sub("", text) if strip_uris else text
    cln = strip_pii_spans(src.strip(), col_name=col_name, strip_placeholders=True)
    cln = re.sub(r"[ \t]{2,}", " ", cln[:max_len]).strip()
    ensure_no_redacted_placeholder(cln, context=ctx)
    return cln


def clean_steward_text(
    text: str | None, *, col_name: str | None = None, max_len: int = 4000
) -> str | None:
    if not text or not text.strip():
        return None
    out = _sanitize(text, col_name=col_name, max_len=max_len, strip_uris=False, ctx="steward")
    return out or None


def strip_uris_and_pii(text: str, *, col_name: str | None = None, max_len: int = 800) -> str:
    if not text or not text.strip():
        return ""
    return _sanitize(text, col_name=col_name, max_len=max_len, strip_uris=True, ctx="rerank")


def _raw_toks(parts: Sequence[str]) -> frozenset[str]:
    return frozenset(t.lower() for p in parts for t in _RAW_ID_RE.findall(p))


def extract_steward_table_context(
    table: TableEvidence, request: GroundingRequest, source: KnowledgeSource
) -> StewardTableContext:
    tn = tuple(n for r in table.steward_notes if (n := clean_steward_text(r)))
    gn = tuple(n for r in request.global_steward_notes if (n := clean_steward_text(r)))
    ov = getattr(source, "steward_notes", ())
    on = (
        tuple(n for r in ov if (n := clean_steward_text(r)))
        if isinstance(ov, Sequence) and not isinstance(ov, (str, bytes)) else ()
    )
    cn = {
        c.column_name: clean_steward_text(c.steward_note, col_name=c.column_name)
        for c in table.columns
    }
    cols = [c.column_name for c in table.columns]
    toks = _raw_toks([table.table_name, *cols, *tn, *gn, *on, *(n for n in cn.values() if n)])
    sec_ok = bool(toks & _SEC_TRIGGERS) and not bool(toks & _SEC_EXCLUSIONS)
    ctx_txt = " ".join(p for p in (table.table_name, *cols, *tn, *on, *gn) if p)[:2000].rstrip()
    return StewardTableContext(
        table.table_id or table.table_name, table.table_name, tn, gn, on, cn, toks, sec_ok, ctx_txt
    )


def build_column_query(
    col: ColumnEvidence, sctx: StewardTableContext, source: KnowledgeSource
) -> tuple[str, list[str], str | None]:
    ct = split_ident_tokens(col.column_name)
    cn, cl = sctx.column_notes.get(col.column_name), col.column_name.strip().lower()
    mt = {t for t in ct if len(t) >= 3 and t not in STOPWORDS}
    snips: list[str] = []
    notes = (*sctx.cleaned_table_notes, *sctx.cleaned_overlay_notes, *sctx.cleaned_global_notes)
    for note in notes:
        for ln in note.splitlines():
            s = ln.strip()
            if s and (cl in s.lower() or (mt & set(split_ident_tokens(s)))):
                if (clip := s[:200].rstrip()) not in snips:
                    snips.append(clip)
                if len(snips) >= 3:
                    break
        if len(snips) >= 3:
            break
    rr = [p for p in (cn, *snips) if p]
    rnote = (
        strip_uris_and_pii(" | ".join(rr), col_name=col.column_name, max_len=600) or None
        if rr else None
    )
    cctx = " ".join(p for p in (sctx.context_text, cn or "", *snips) if p)
    exp = source.expand_query_terms(ct, context_text=cctx)
    if "sec" in ct and not sctx.sec_expansion_allowed:
        rc = _raw_toks([cctx])
        if not (bool(rc & _SEC_TRIGGERS) and not bool(rc & _SEC_EXCLUSIONS)):
            exp = [t for t in exp if t.lower() not in _SEC_INJECTED]
    utoks = list(dict.fromkeys(t.lower() for t in exp if t.strip()))
    return (col.column_name.strip(), utoks, rnote)


def build_table_anchor_query(
    table: TableEvidence, sctx: StewardTableContext, source: KnowledgeSource
) -> tuple[str, str]:
    toks = split_ident_tokens(_SRC_PREF_RE.sub("", table.table_name.strip()))
    sing = [_singularize(t) for t in toks]
    rq = " ".join(toks)
    sq = " ".join(sing) or rq
    exp = source.expand_query_terms(sing, context_text=sctx.context_text)
    return (sq, " ".join(dict.fromkeys([*sing, *(t.lower() for t in exp if t)])) or rq)


def check_etl_prescreen(col: ColumnEvidence, table_name: str) -> tuple[str | None, str | None]:
    cl = col.column_name.strip().lower()
    if cl in _KEEP_TEMP:
        return (None, None)
    if (
        cl in _AUDIT_COLS or cl.startswith(("etl_", "_etl_"))
        or cl.endswith(("_hash_diff", "_hashdiff"))
    ):
        return ("system_audit_metadata", "ETL, CDC, or SCD2 audit metadata column.")
    if (
        cl == "batch_id" and not col.is_pk and not col.fk_ref
        and not any(_singularize(t) == "batch" for t in split_ident_tokens(table_name))
    ):
        return ("system_audit_metadata", "Non-PK/non-FK batch audit identifier column.")
    return (None, None)


def profile_column_evidence(
    col: ColumnEvidence, table: TableEvidence
) -> tuple[InferredValueProfile, list[str]]:
    decl = map_sql_to_portable_type(col.data_type)
    nn = [v for v in col.sample_values if v is not None]
    mk, unm = 0, []
    for v in nn:
        sv = str(v)
        if sv == MASKED_PII_TOKEN or has_redacted_placeholder(sv):
            mk += 1
        else:
            unm.append(sv)
    vt = [tag for v in unm for tag in detect_pii_in_value(v, col_name=col.column_name)]
    pii = sorted(set(col.pii_tags) | set(detect_pii_in_column_name(col.column_name)) | set(vt))
    if col.precomputed_shape is not None:
        sh = col.precomputed_shape
    else:
        samps = [
            None if v is None else (MASKED_PII_TOKEN if has_redacted_placeholder(str(v)) else v)
            for v in col.sample_values
        ]
        sh = compute_column_shape_stats(
            samps, col_name=col.column_name, sql_type=col.data_type, pii_tags=pii,
            row_count_est=table.row_count_est, distinct_count_est=col.distinct_count_est,
            null_ratio=col.null_ratio,
        )
    if pii and (not sh.pii_suppressed or sh.observed_enum_values):
        sh = sh.model_copy(update={
            "pii_suppressed": True, "length_min": None, "length_max": None,
            "numeric_min": None, "numeric_max": None, "observed_enum_values": [],
        })
    inf, tc = (decl, 1.0)
    if decl == "UTF8_STRING":
        if sh.structural_patterns.get("iso_timestamp", 0.0) >= 0.90:
            inf, tc = ("TIMESTAMP_UTC_US", 0.95)
        elif sh.structural_patterns.get("iso_date", 0.0) >= 0.90:
            inf, tc = ("DATE32", 0.95)
    return (InferredValueProfile(
        observed_sample_count=len(col.sample_values), non_null_count=len(nn), masked_count=mk,
        declared_logical_type=decl, inferred_logical_type=inf, type_confidence=tc,
        pii_tags=pii, shape=sh,
    ), unm)


def _pref(toks: Sequence[str], suffixes: frozenset[str]) -> str | None:
    return "_".join(toks[:-1]) if toks and toks[-1] in suffixes else None


def detect_paired_columns(
    table: TableEvidence, profs: Mapping[str, InferredValueProfile],
    top_ch: Mapping[str, str | None], prescr: Mapping[str, str | None],
    *, include_sole_ccy: bool = False, include_alt_keys: bool = False,
) -> list[ColumnPair]:
    pairs: list[ColumnPair] = []
    act = [c for c in table.columns if prescr.get(c.column_name) is None]
    if len(pks := [c for c in act if c.is_pk]) >= 2:
        opks = sorted(pks, key=lambda c: (c.pk_ord is None, c.pk_ord or 0, c.column_name))
        pairs.append(ColumnPair(kind="composite_key", columns=[c.column_name for c in opks]))
    by_low = {c.column_name.strip().lower(): c.column_name for c in act}
    pairs.extend(
        ColumnPair(kind="effective_interval", columns=[by_low[s], by_low[e]])
        for s, e in _INTERVALS if s in by_low and e in by_low
    )
    toks_m = {c.column_name: split_ident_tokens(c.column_name) for c in act}
    ccys, amts = [], []
    for c in act:
        cn, tks = c.column_name, toks_m[c.column_name]
        loc = (top_ch.get(cn) or "").rsplit("/", 1)[-1]
        if (
            profs[cn].shape.structural_patterns.get("iso_4217_ccy", 0.0) >= 0.80
            or loc in ("Currency", "CurrencyIdentifier") or (tks and tks[-1] in _CCY_TOKS)
        ):
            ccys.append(cn)
            continue
        dt, ts = profs[cn].declared_logical_type, set(tks)
        if (
            (dt in ("INT32", "INT64") or isinstance(dt, DecimalType))
            and not (ts & _NON_CCY_TOKS)
            and ((ts & _AMT_TOKS) or loc in ("MonetaryAmount", "MonetaryPrice"))
        ):
            amts.append(cn)
    ma: set[str] = set()
    for an in amts:
        if ap := _pref(toks_m[an], _AMT_TOKS):
            for cn in ccys:
                if _pref(toks_m[cn], _CCY_TOKS) == ap:
                    pairs.append(ColumnPair(kind="amount_currency", columns=[an, cn]))
                    ma.add(an)
                    break
    if include_sole_ccy and len(ccys) == 1:
        pairs.extend(
            ColumnPair(kind="amount_currency", columns=[an, ccys[0]]) for an in amts if an not in ma
        )
    if include_alt_keys:
        owners = [
            c.column_name for c in act
            if not c.fk_ref and not profs[c.column_name].pii_tags
            and not c.column_name.strip().lower().startswith(FOREIGN_ROLE_PREFIXES)
            and (c.is_pk or profs[c.column_name].shape.distinct_ratio >= 0.95)
            and (
                c.is_pk or profs[c.column_name].shape.surrogate_kind == "prefixed_operational_id"
                or any(
                    profs[c.column_name].shape.structural_patterns.get(k, 0.0) >= 0.90
                    for k in NON_PII_BK_PATTERNS
                )
            )
        ]
        if len(owners) >= 2:
            pairs.append(ColumnPair(kind="alternative_keys", columns=owners))
    return pairs


def build_candidate_channel_inputs(
    source: KnowledgeSource, col: ColumnEvidence, prof: InferredValueProfile,
    merged: Sequence[RecallHit], tsc: Mapping[str, float], ssc: Mapping[str, float],
    evmap: Mapping[str, BackendReasoningResult], bq: str,
    aligns: Callable[[str | None, str, set[str], set[str]], bool], *, anc_on: bool,
    top_uri: str | None, top_ancs: set[str], top_played: set[str],
    has_chk: bool, has_nochk: bool, is_bool: bool,
) -> list[CandidateChannelInputs]:
    has_bic = prof.shape.structural_patterns.get("swift_bic", 0.0) >= 0.50
    clow = col.column_name.strip().lower()
    cands: list[CandidateChannelInputs] = []
    for h in merged:
        eid = h.entry_id
        it, ev = source.get_item(eid), evmap.get(eid)
        sem = (ev.semantic_context if ev and ev.semantic_context else None) or (
            it.semantic_context if it is not None else None
        )
        if sem is None:
            continue
        lex = max(tsc.get(eid, 0.0), float(source.score(bq, eid)))
        is_s = eid in ssc
        it_s = bool(it and (
            it.metadata.get("has_surface_rule") or it.metadata.get("allowed_values")
            or it.metadata.get("pattern") or it.semantic_context.standard_authority
        ))
        if is_s:
            sv = ssc[eid]
            surf = (
                0.6 if (has_bic and not has_chk and eid.endswith("BusinessIdentifierCode"))
                else (1.0 if sv >= 0.85 else (0.6 if sv >= 0.60 else 0.2))
            )
        else:
            surf = -0.8 if (it_s and ev and ev.shape_range_score < -0.5) else 0.0
        banc = 0.0
        if anc_on and top_uri is not None:
            ents = [sem.domain_entity_uri, sem.identified_entity_uri]
            if it is not None:
                ents.extend(it.neighborhood_links.get("identified_entity_uris", ()))
                ents.extend(it.neighborhood_links.get("domain", ()))
            if any(aligns(u, top_uri, top_ancs, top_played) for u in ents):
                banc = 1.0
            elif (
                sem.archetype != "identifier" and ev and "disjoint_with_anchor" in ev.support_detail
            ):
                banc = -0.7
        corr = True
        if has_nochk and not has_chk:
            corr = (
                bool(top_uri is not None and banc > 0.0 and lex >= 0.65)
                if has_bic else bool(lex >= 0.65 or banc > 0.0)
            )
        lbl = it.label if it is not None else h.label
        als = tuple(it.aliases) if it is not None else ()
        ct = normalize_column_tokens(
            col.column_name, archetype=sem.archetype, is_boolean_datatype=is_bool
        )
        lt = normalize_property_label(lbl)
        at = [normalize_property_label(a) for a in als if a.strip()]
        is_q = ct != lt and (len(ct) > len(lt) or clow.startswith(FOREIGN_ROLE_PREFIXES))
        cands.append(CandidateChannelInputs(
            eid, lbl, h.entry_kind, h.origin_kind, sem, als,
            tuple(it.replaces_uris) if it is not None else (), lex, surf, is_s or it_s,
            ev.shape_range_score if ev else 0.0, ev.hard_veto if ev else False, banc,
            ev.inverse_direction_score if ev else 0.0,
            ev.suggested_predicate_hint if ev else None, "", ev.support_detail if ev else "",
            "", corr, is_bool, is_q, ct != lt and ct not in at,
        ))
    return cands


__all__ = [
    "FOREIGN_ROLE_PREFIXES", "NON_PII_BK_PATTERNS", "StewardTableContext",
    "build_candidate_channel_inputs", "build_column_query", "build_table_anchor_query",
    "check_etl_prescreen", "clean_steward_text", "detect_paired_columns",
    "extract_steward_table_context", "profile_column_evidence", "split_ident_tokens",
    "strip_uris_and_pii",
]
