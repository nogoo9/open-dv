"""Shared slug, pluralization, ontology coverage, and key resolution helpers."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

_STATIC_LOOKUP_SUFFIXES = ("_code", "_status", "_type", "_iso", "_enum")
_NATURAL_BK_SUFFIXES = ("_id", "_code", "_key", "_number", "_lei", "_isin", "_cusip", "_cik")
_SURROGATE_PK_RE = re.compile(
    r"(^|_)(sk|seq|row_seq|seq_id|row_id|surrogate|autogen)(_id)?$", re.IGNORECASE
)
_DS_WAVE_PREFIX_RE = re.compile(
    r"^(?:(?:ds|src|feed|tbl|stg|bronze)\d+(?:_w\d+)?_+)+", re.IGNORECASE
)
_GENERIC_TABLE_WORDS = frozenset(
    {
        "data",
        "feed",
        "table",
        "tbl",
        "src",
        "info",
        "list",
        "detail",
        "details",
        "log",
        "staging",
        "stg",
        "raw",
        "vw",
        "crosswalk",
        "xref",
        "mapping",
        "map",
        "bridge",
        "lookup",
    }
)


def _slugify(name: str) -> str:
    """Normalize an arbitrary concept or table label to a lowercase underscore slug."""
    cleaned = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
    return cleaned or "entity"


def _strip_dataset_wave_prefix(slug: str) -> str:
    """Strip numeric dataset/wave prefixes like `ds02_w2_` or `ds04_` from table slugs."""
    stripped = _DS_WAVE_PREFIX_RE.sub("", _slugify(slug)).strip("_")
    return stripped or _slugify(slug)


def _attr_name(attr: Any) -> str | None:
    if isinstance(attr, dict):
        val = attr.get("attribute_name") or attr.get("name")
        return str(val) if val else None
    val = getattr(attr, "attribute_name", None) or getattr(attr, "name", None)
    return str(val) if val else None


def _concept_tokens_and_attrs(cbc: Any) -> tuple[set[str], set[str]]:
    tokens: set[str] = set()
    for raw in (
        str(getattr(cbc, "concept_name", "") or "").lower().replace("cbc_", ""),
        str(getattr(cbc, "concept_uri", "") or "").rsplit(":", 1)[-1],
        str(getattr(cbc, "target_hub", "") or "").lower().removeprefix("hub_"),
        *[str(s) for s in (getattr(cbc, "synonyms", None) or [])],
    ):
        s_val = _slugify(raw) if raw else ""
        if s_val and s_val != "entity":
            for part in s_val.split("_"):
                if part and part not in _GENERIC_TABLE_WORDS:
                    tokens.update({part, _singularize_word(part)})
    attrs: set[str] = set()
    for attr in getattr(cbc, "attributes", None) or []:
        aname = _attr_name(attr)
        if aname:
            attrs.add(_slugify(aname))
    return tokens, attrs


def _singularize_word(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return f"{word[:-3]}y"
    if len(word) > 4 and word.endswith(("ches", "shes", "xes", "zes", "sses")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is", "as")):
        return word[:-1]
    return word


def _singularize_slug(slug: str) -> str:
    """Singularize a normalized snake_case slug (e.g., securities -> security)."""
    parts = [p for p in _slugify(slug).split("_") if p]
    if not parts:
        return "entity"
    return "_".join([*parts[:-1], _singularize_word(parts[-1])])


def _is_static_lookup_feed(
    feed: Any,
    t_slug: str,
    fk_targets: set[str],
    ont_attrs: set[str],
) -> bool:
    row_cnt = int(getattr(feed, "row_count", 0) or 0)
    fks = getattr(feed, "foreign_keys", None) or []
    pks = list(getattr(feed, "primary_keys", None) or [])
    if not (0 < row_cnt <= 250) or fks or len(pks) != 1:
        return False
    pk_slug = _slugify(str(pks[0]))
    if not (pk_slug.endswith(_STATIC_LOOKUP_SUFFIXES) or pk_slug == t_slug):
        return False
    return t_slug in fk_targets or pk_slug in ont_attrs or t_slug in ont_attrs


def _collect_ontology_coverage_index(
    ontology: Any, discovered_feeds: Sequence[Any]
) -> tuple[set[str], list[str], set[str], set[str], set[str]]:
    cbcs = getattr(ontology, "canonical_business_concepts", []) or []
    refs = getattr(ontology, "reference_enumerations", []) or []
    ont_hubs = {
        cbc.target_hub.lower() for cbc in cbcs if getattr(cbc, "target_hub", None)
    } | {
        ref.target_reference_table.lower()
        for ref in refs
        if getattr(ref, "target_reference_table", None)
    }
    ont_concepts: list[str] = []
    ont_slugs: set[str] = set()
    ont_attrs: set[str] = set()
    for cbc in cbcs:
        raw_name = str(getattr(cbc, "concept_name", "") or "").lower().replace("cbc_", "")
        if raw_name:
            ont_concepts.append(raw_name)
            s_name = _slugify(raw_name)
            ont_slugs.update({s_name, _singularize_slug(s_name)})
        c_uri = str(getattr(cbc, "concept_uri", "") or "")
        if ":" in c_uri:
            u_slug = _slugify(c_uri.rsplit(":", 1)[-1])
            ont_slugs.update({u_slug, _singularize_slug(u_slug)})
        th = str(getattr(cbc, "target_hub", "") or "").lower().removeprefix("hub_")
        if th:
            ont_slugs.update({_slugify(th), _singularize_slug(th)})
        for syn in getattr(cbc, "synonyms", None) or []:
            s_syn = _slugify(str(syn))
            ont_slugs.update({s_syn, _singularize_slug(s_syn)})
        for attr in getattr(cbc, "attributes", None) or []:
            aname = _attr_name(attr)
            if aname:
                ont_attrs.add(_slugify(aname))
    for ref in refs:
        e_uri = str(getattr(ref, "enum_uri", "") or "")
        if ":" in e_uri:
            e_slug = _slugify(e_uri.rsplit(":", 1)[-1])
            ont_slugs.update({e_slug, _singularize_slug(e_slug)})
        t_ref = str(getattr(ref, "target_reference_table", "") or "").lower().removeprefix("ref_")
        if t_ref:
            ont_slugs.update({_slugify(t_ref), _singularize_slug(t_ref)})
        e_name = str(getattr(ref, "enumeration_name", "") or "")
        if e_name:
            en_slug = _slugify(e_name)
            ont_slugs.update({en_slug, _singularize_slug(en_slug)})
    fk_targets = {
        _slugify(str(fk.get("target_table") or "").rsplit(".", 1)[-1])
        for fd in discovered_feeds
        for fk in (getattr(fd, "foreign_keys", None) or [])
        if isinstance(fk, dict) and fk.get("target_table")
    }
    return ont_hubs, ont_concepts, ont_slugs, ont_attrs, fk_targets


def _is_feed_covered_by_ontology(
    feed: Any,
    src_in: Any,
    ont_hubs: set[str],
    ont_concepts: list[str],
    ont_slugs: set[str],
    ont_attrs: set[str],
    fk_targets: set[str],
    ontology: Any = None,
) -> bool:
    tgt_hub = (
        getattr(src_in, "target_hub", None)
        or getattr(src_in, "target_hub_name", None)
        or getattr(feed, "target_hub_name", None)
        or ""
    ).lower()
    if tgt_hub and tgt_hub in ont_hubs:
        return True
    if tgt_hub and tgt_hub not in ont_hubs:
        return False
    t_name = str(feed.table_name).lower()
    t_slug = _slugify(t_name.rsplit(".", 1)[-1])
    t_sing = _singularize_slug(t_slug)
    t_clean = _strip_dataset_wave_prefix(t_slug)
    t_clean_sing = _singularize_slug(t_clean)
    if {t_slug, t_sing, t_clean, t_clean_sing} & ont_slugs:
        return True
    t_clean_words = t_clean.replace("_", " ")
    if any(c and (c in t_name or c in t_clean_words) for c in ont_concepts):
        return True
    if ontology is not None:
        t_tokens = {
            w
            for p in t_clean.split("_")
            if p and p not in _GENERIC_TABLE_WORDS
            for w in (p, _singularize_word(p))
        }
        f_cols = {
            _slugify(str(cd.get("name")))
            for cd in (getattr(feed, "column_descriptors", None) or [])
            if isinstance(cd, dict) and cd.get("name")
        }
        for cbc in getattr(ontology, "canonical_business_concepts", None) or []:
            c_tokens, c_attrs = _concept_tokens_and_attrs(cbc)
            if (
                t_tokens
                and c_tokens
                and (t_tokens & c_tokens)
                and (not c_attrs or not f_cols or (f_cols & c_attrs) or t_tokens <= c_tokens)
            ):
                return True
            if (
                len(f_cols) >= 2
                and c_attrs
                and len(f_cols & c_attrs) >= max(2, (len(f_cols) + 1) // 2)
            ):
                return True
    return _is_static_lookup_feed(feed, t_slug, fk_targets, ont_attrs)


def _resolve_non_surrogate_bks(
    pks: Sequence[str],
    col_descs: Sequence[dict[str, Any]],
    fks: Sequence[dict[str, Any]],
    ontology: Any,
    uri: str,
    h_name: str,
    t_slug: str,
) -> list[str]:
    """Replace monotonic surrogate PKs (e.g. row_seq_sk) when a natural BK exists."""
    bk_cols = list(pks) if pks else []
    if not bk_cols or not all(_SURROGATE_PK_RE.search(c) for c in bk_cols):
        return bk_cols
    fk_cols = {
        str(f.get("fk_column") or f.get("source_column") or "")
        for f in fks
        if f.get("fk_column") or f.get("source_column")
    }
    non_surr = [
        str(cd["name"])
        for cd in col_descs
        if cd.get("name")
        and not _SURROGATE_PK_RE.search(str(cd["name"]))
        and str(cd["name"]) not in fk_cols
    ]
    if not non_surr:
        return bk_cols
    non_surr_set = set(non_surr)
    hub_slug = h_name.removeprefix("hub_")
    if ontology:
        for cbc in getattr(ontology, "canonical_business_concepts", []):
            if cbc.concept_uri != uri and cbc.target_hub != h_name:
                continue
            kat = getattr(cbc, "key_authority_tiers", None)
            if kat:
                for tier_attr in (
                    "tier_1_global_standard",
                    "tier_2_enterprise_sor",
                    "tier_3_local_scoped",
                ):
                    t_obj = getattr(kat, tier_attr, None)
                    for pat in getattr(t_obj, "column_patterns", None) or []:
                        if pat in non_surr_set:
                            return [pat]
            attrs = [
                str(a.attribute_name)
                for a in (getattr(cbc, "attributes", None) or [])
                if getattr(a, "attribute_name", None) in non_surr_set
            ]
            for pref in (f"{hub_slug}_id", f"{t_slug}_id", f"{hub_slug}_code", f"{t_slug}_code"):
                if pref in attrs:
                    return [pref]
            attr_bk = next((a for a in attrs if a.lower().endswith(_NATURAL_BK_SUFFIXES)), None)
            if attr_bk:
                return [attr_bk]
    for pref in (f"{hub_slug}_id", f"{t_slug}_id", f"{hub_slug}_code", f"{t_slug}_code"):
        if pref in non_surr_set:
            return [pref]
    cand = next((c for c in non_surr if c.lower().endswith(_NATURAL_BK_SUFFIXES)), None)
    return [cand] if cand else bk_cols


def _annotate_enum_candidate_roles(
    col_stats: dict[str, Any],
    table_name: str,
    pks: Sequence[str],
    fks: Sequence[Any],
    row_cnt: int,
) -> dict[str, Any]:
    """Tag static low-cardinality lookup PKs with ENUM_REFERENCE_CODE for Alg A.2."""
    if not (0 < row_cnt <= 250) or fks or len(pks) != 1:
        return col_stats
    pk = str(pks[0])
    m = col_stats.get(pk)
    if not isinstance(m, dict):
        return col_stats
    dist = int(m.get("distinct_count", m.get("approx_distinct", 9999)))
    t_slug = _slugify(table_name.rsplit(".", 1)[-1])
    pk_slug = _slugify(pk)
    if 0 < dist <= 250 and (pk_slug.endswith(_STATIC_LOOKUP_SUFFIXES) or pk_slug == t_slug):
        m["candidate_role"] = "ENUM_REFERENCE_CODE"
        m["key_growth_30d"] = 0.0
    return col_stats


__all__ = [
    "_annotate_enum_candidate_roles",
    "_collect_ontology_coverage_index",
    "_is_feed_covered_by_ontology",
    "_resolve_non_surrogate_bks",
    "_singularize_slug",
    "_slugify",
]
