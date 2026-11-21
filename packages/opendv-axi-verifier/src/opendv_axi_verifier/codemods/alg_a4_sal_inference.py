"""Domain-agnostic ontology key-tier, natural BK, relationship FK, and SAL inference."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

_DS_WAVE_PREFIX_RE = re.compile(
    r"^(?:(?:ds|src|feed|tbl|stg|bronze)\d+(?:_w\d+)?_+)+", re.IGNORECASE
)
_SURROGATE_RE = re.compile(
    r"(^|_)(sk|seq|row_seq|seq_id|row_id|surrogate|autogen)(_id)?$", re.IGNORECASE
)
_AUDIT_COLS = frozenset(
    {
        "load_dts",
        "rec_src",
        "record_source",
        "applied_dts",
        "created_at",
        "updated_at",
        "ingested_at",
    }
)
_BK_SUFFIXES = (
    "_id",
    "_code",
    "_key",
    "_number",
    "_lei",
    "_isin",
    "_cusip",
    "_cik",
    "_sedol",
    "_figi",
)
_GLOBAL_SUFFIXES = ("lei", "isin", "cusip", "duns", "figi", "sedol", "lei_code")
_SAL_MASTER_PREFIXES = ("master_", "primary_", "canonical_", "surviving_", "survivor_", "golden_")
_SAL_DUP_PREFIXES = ("duplicate_", "alias_", "secondary_", "merged_", "dup_", "alt_")


def _slug(val: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(val).lower()).strip("_") or "entity"


def _strip_ds_prefix(val: str) -> str:
    return _DS_WAVE_PREFIX_RE.sub("", _slug(val)).strip("_") or _slug(val)


def _ont_attr_name(attr: Any) -> str | None:
    if isinstance(attr, dict):
        v = attr.get("attribute_name") or attr.get("name")
        return str(v) if v else None
    v = getattr(attr, "attribute_name", None) or getattr(attr, "name", None)
    return str(v) if v else None


def get_cbcs(ontology: Any) -> list[Any]:
    if ontology is None:
        return []
    if isinstance(ontology, dict):
        return list(ontology.get("canonical_business_concepts") or [])
    return list(getattr(ontology, "canonical_business_concepts", None) or [])


def get_rels(ontology: Any) -> list[Any]:
    if ontology is None:
        return []
    if isinstance(ontology, dict):
        return list(ontology.get("canonical_relationships") or [])
    return list(getattr(ontology, "canonical_relationships", None) or [])


def _cbc_field(cbc: Any, key: str, default: Any = "") -> Any:
    return cbc.get(key, default) if isinstance(cbc, dict) else getattr(cbc, key, default)


def _cbc_attrs(cbc: Any) -> set[str]:
    attrs = _cbc_field(cbc, "attributes", None) or []
    return {_slug(n) for a in attrs if (n := _ont_attr_name(a))}


def _rel_field(rel: Any, key: str, default: Any = "") -> Any:
    return rel.get(key, default) if isinstance(rel, dict) else getattr(rel, key, default)


def infer_key_tier_from_ontology(
    cand_cols: Sequence[str],
    ontology: Any,
    uri: str = "",
    h_name: str = "",
    eff_rec_src: str = "SRC",
) -> tuple[str, str]:
    cand_low = [c.lower() for c in cand_cols if c]
    if any(any(c.endswith(s) for s in _GLOBAL_SUFFIXES) for c in cand_low):
        return "TIER_1_GLOBAL_STANDARD", "GLOBAL"
    for cbc in get_cbcs(ontology):
        c_uri = str(_cbc_field(cbc, "concept_uri", "") or "")
        c_hub = str(_cbc_field(cbc, "target_hub", "") or "")
        c_name = str(_cbc_field(cbc, "concept_name", "") or "")
        if not (
            (not uri and not h_name)
            or (uri and c_uri == uri)
            or (h_name and (c_hub == h_name or c_name in h_name))
        ):
            continue
        kat = _cbc_field(cbc, "key_authority_tiers", None)
        if not kat:
            continue
        for attr, tier in (
            ("tier_1_global_standard", "TIER_1_GLOBAL_STANDARD"),
            ("tier_2_enterprise_sor", "TIER_2_ENTERPRISE_SOR"),
            ("tier_3_local_scoped", "TIER_3_LOCAL_SCOPED"),
        ):
            t_obj = _cbc_field(kat, attr, None)
            pats = _cbc_field(t_obj, "column_patterns", None) or [] if t_obj else []
            if t_obj and any(str(p).lower() in cand_low for p in pats):
                pol = _cbc_field(t_obj, "bkcc_policy", None)
                bkcc = (
                    str(pol)
                    if pol
                    and str(pol).upper() not in {"NONE", "GLOBAL", "SOURCE_SYSTEM_PREFIX_REQUIRED"}
                    else (eff_rec_src if tier == "TIER_3_LOCAL_SCOPED" else "GLOBAL")
                )
                return tier, bkcc
    return "TIER_3_LOCAL_SCOPED", eff_rec_src


def extract_profile_col_stats(
    profile_summaries: dict[str, Any] | None,
    *table_keys: str,
) -> dict[str, dict[str, Any]]:
    """Normalize per-column stats from Stage 01 `profile_summaries` or Graph B dicts."""
    if not profile_summaries or not isinstance(profile_summaries, dict):
        return {}
    candidates = [k for k in table_keys if k]
    base_candidates = {k.rsplit(".", 1)[-1].lower() for k in candidates}
    matched_tbl: dict[str, Any] | None = next(
        (profile_summaries[k] for k in candidates if isinstance(profile_summaries.get(k), dict)),
        None,
    )
    if matched_tbl is None:
        matched_tbl = next(
            (
                pv
                for pk, pv in profile_summaries.items()
                if isinstance(pv, dict) and pk.rsplit(".", 1)[-1].lower() in base_candidates
            ),
            None,
        )
    if matched_tbl is not None:
        row_cnt = matched_tbl.get("row_count")
        col_stats = matched_tbl.get("column_stats")
        src = col_stats if isinstance(col_stats, dict) else matched_tbl
        return {
            str(cn): {**dict(st), "row_count": st.get("row_count", row_cnt)}
            for cn, st in src.items()
            if isinstance(st, dict)
            and (
                isinstance(col_stats, dict)
                or any(
                    m in st for m in ("non_null_ratio", "u_non_null", "null_rate", "distinct_count")
                )
            )
        }
    for ss in profile_summaries.get("source_systems", []) or []:
        if not isinstance(ss, dict):
            continue
        for tbl in ss.get("tables", []) or []:
            if not isinstance(tbl, dict):
                continue
            bt = str(tbl.get("bronze_table") or "").lower()
            if bt in {k.lower() for k in candidates} or bt.rsplit(".", 1)[-1] in base_candidates:
                return {
                    str(col["column_name"]): dict(col)
                    for col in tbl.get("columns", []) or []
                    if isinstance(col, dict) and col.get("column_name")
                }
    return {}


def resolve_best_concept_by_columns(
    table_name: str,
    col_descs: Sequence[dict[str, Any]],
    ontology: Any,
    default_hub: str,
    default_uri: str,
) -> tuple[str, str]:
    """Disambiguate target concept/hub using stripped table tokens and attribute overlap."""
    cbcs = get_cbcs(ontology)
    if not cbcs:
        return default_hub, default_uri
    f_cols = {_slug(cd["name"]) for cd in col_descs if isinstance(cd, dict) and cd.get("name")}
    t_clean = _strip_ds_prefix(table_name.rsplit(".", 1)[-1])
    t_tokens = set(t_clean.split("_"))
    best_score = -1
    best_pair = (default_hub, default_uri)
    for cbc in cbcs:
        c_uri = str(_cbc_field(cbc, "concept_uri", "") or "")
        c_hub = str(_cbc_field(cbc, "target_hub", "") or f"hub_{_slug(c_uri.rsplit(':', 1)[-1])}")
        c_name = str(_cbc_field(cbc, "concept_name", "") or "")
        c_slug = _slug(c_name or c_hub.removeprefix("hub_"))
        c_tokens = set(c_slug.split("_")) | set(c_hub.removeprefix("hub_").lower().split("_"))
        for syn in _cbc_field(cbc, "synonyms", None) or []:
            c_tokens |= set(_slug(str(syn)).split("_"))
        tok_hits = len(t_tokens & c_tokens)
        attr_hits = len(f_cols & _cbc_attrs(cbc))
        if tok_hits == 0 and attr_hits < 2:
            continue
        score = (
            tok_hits * 12 + attr_hits * 8 + (20 if c_slug == t_clean or c_hub == default_hub else 0)
        )
        if score > best_score:
            best_score = score
            best_pair = (c_hub, c_uri)
    return best_pair


def detect_crosswalk_sal_spec(
    col_descs: Sequence[dict[str, Any]],
    uri: str,
    h_name: str,
    ontology: Any,
) -> dict[str, Any] | None:
    """Detect a self-referential `[E, E]` Same-As crosswalk relationship on a feed."""
    col_names = [str(cd["name"]) for cd in col_descs if isinstance(cd, dict) and cd.get("name")]
    if len(col_names) < 2:
        return None
    low_map = {c.lower(): c for c in col_names}
    self_rel = next(
        (
            rel
            for rel in get_rels(ontology)
            if (
                len(eps := [str(e) for e in (_rel_field(rel, "endpoints", None) or [])]) == 2
                and eps[0] == eps[1]
                and (eps[0] == uri or not uri)
            )
            or (
                str(_rel_field(rel, "target_link", "") or "").startswith("sal_")
                and h_name.removeprefix("hub_") in str(_rel_field(rel, "target_link", "") or "")
            )
        ),
        None,
    )
    if self_rel is None:
        return None
    master_col = next(
        (c for c in col_names if any(c.lower().startswith(p) for p in _SAL_MASTER_PREFIXES)), None
    )
    dup_col = next(
        (
            c
            for c in col_names
            if c != master_col and any(c.lower().startswith(p) for p in _SAL_DUP_PREFIXES)
        ),
        None,
    )
    if not (master_col and dup_col):
        target_cbc = next(
            (c for c in get_cbcs(ontology) if _cbc_field(c, "concept_uri") == uri), None
        )
        cbc_attrs = _cbc_attrs(target_cbc) if target_cbc is not None else set()
        key_cands = [
            c
            for c in col_names
            if not _SURROGATE_RE.search(c)
            and c.lower() not in _AUDIT_COLS
            and (c.lower() in cbc_attrs or c.lower().endswith(_BK_SUFFIXES))
        ]
        if len(key_cands) >= 2 and not master_col:
            master_col, dup_col = key_cands[0], key_cands[1]
        elif master_col and not dup_col:
            dup_col = next((c for c in key_cands if c != master_col), None)
    if not (master_col and dup_col and master_col != dup_col):
        return None
    raw_tlink = str(_rel_field(self_rel, "target_link", "") or "")
    sal_name = raw_tlink if raw_tlink.startswith("sal_") else f"sal_{h_name.removeprefix('hub_')}"
    rel_uri = str(
        _rel_field(self_rel, "relationship_uri", "") or f"urn:ontology:relationship:{sal_name}"
    )
    return {
        "sal_name": sal_name,
        "rel_uri": rel_uri,
        "target_hub": h_name,
        "master_col": low_map.get(master_col.lower(), master_col),
        "dup_col": low_map.get(dup_col.lower(), dup_col),
    }


def infer_natural_bk(
    col_descs: Sequence[dict[str, Any]],
    col_stats: dict[str, dict[str, Any]],
    fks: Sequence[dict[str, Any]],
    ontology: Any,
    uri: str,
    h_name: str,
    t_slug: str,
) -> list[str]:
    """Infer natural non-surrogate BK from attributes, naming, and profile stats when PKs empty."""
    fk_cols = {
        str(fk.get("fk_column") or fk.get("source_column") or "").lower()
        for fk in fks
        if isinstance(fk, dict)
    }
    target_cbc = next(
        (
            c
            for c in get_cbcs(ontology)
            if _cbc_field(c, "concept_uri") == uri or _cbc_field(c, "target_hub") == h_name
        ),
        None,
    )
    cbc_attrs = _cbc_attrs(target_cbc) if target_cbc is not None else set()
    hub_tokens = set(h_name.removeprefix("hub_").lower().split("_")) | set(
        _strip_ds_prefix(t_slug).split("_")
    )
    best_col: str | None = None
    best_score = -1
    for cd in col_descs:
        if not isinstance(cd, dict):
            continue
        cname = str(cd.get("name") or "")
        clow = cname.lower()
        if (
            not cname
            or clow in fk_cols
            or clow in _AUDIT_COLS
            or _SURROGATE_RE.search(cname)
            or not clow.endswith(_BK_SUFFIXES)
        ):
            continue
        st = col_stats.get(cname) or col_stats.get(clow) or {}
        score = 30
        if clow in cbc_attrs:
            score += 40
        if any(tok and tok in clow for tok in hub_tokens):
            score += 35
        if st.get("non_null_ratio") is not None and float(st["non_null_ratio"]) >= 1.0:
            score += 10
        if (
            st.get("distinct_count") is not None
            and st.get("row_count")
            and int(st["distinct_count"]) == int(st["row_count"])
        ):
            score += 25
        if score > best_score:
            best_score = score
            best_col = cname
    return [best_col] if best_col else []


def infer_kat_bk_and_fks(
    col_descs: Sequence[dict[str, Any]],
    bk_cols: list[str],
    fks: list[dict[str, Any]],
    ontology: Any,
    uri: str,
    h_name: str,
    hubs_by_name: dict[str, Any] | None = None,
    *,
    skip_sal: bool = False,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Infer BK from `key_authority_tiers` and FKs from tiers + `canonical_relationships`."""
    cbcs = get_cbcs(ontology)
    if not cbcs:
        return bk_cols, fks
    cnames = [
        str(cd.get("name") or "") for cd in col_descs if isinstance(cd, dict) and cd.get("name")
    ]
    cname_set = set(cnames)
    if not bk_cols:
        for cbc in cbcs:
            kat = _cbc_field(cbc, "key_authority_tiers", None)
            if kat and (
                _cbc_field(cbc, "concept_uri") == uri or _cbc_field(cbc, "target_hub") == h_name
            ):
                pats = [
                    p
                    for attr in (
                        "tier_1_global_standard",
                        "tier_2_enterprise_sor",
                        "tier_3_local_scoped",
                    )
                    if (t_obj := _cbc_field(kat, attr, None))
                    for p in (_cbc_field(t_obj, "column_patterns", None) or [])
                ]
                m = next((p for p in pats if p in cname_set), None)
                if m:
                    bk_cols = [m]
                    break
    if skip_sal:
        return bk_cols, fks

    seen_fks = {
        str(f.get("fk_column") or f.get("source_column") or "") for f in fks if isinstance(f, dict)
    }
    for cname in cnames:
        if cname in bk_cols or cname in seen_fks:
            continue
        for cbc in cbcs:
            c_hub = str(_cbc_field(cbc, "target_hub", "") or "")
            if (
                _cbc_field(cbc, "concept_uri") == uri
                or c_hub == h_name
                or (c_hub == "hub_document" and cname != "doc_id")
            ):
                continue
            kat = _cbc_field(cbc, "key_authority_tiers", None)
            if kat:
                pats = {
                    p
                    for attr in (
                        "tier_1_global_standard",
                        "tier_2_enterprise_sor",
                        "tier_3_local_scoped",
                    )
                    if (t_obj := _cbc_field(kat, attr, None))
                    for p in (_cbc_field(t_obj, "column_patterns", None) or [])
                }
                if cname in pats:
                    fks.append(
                        {
                            "fk_column": cname,
                            "target_table": c_hub.removeprefix("hub_"),
                            "target_column": cname,
                        }
                    )
                    seen_fks.add(cname)
                    break

    fks = infer_ontology_fks_for_feed(col_descs, bk_cols, fks, uri, h_name, ontology, hubs_by_name)
    return bk_cols, fks


def infer_ontology_fks_for_feed(
    col_descs: Sequence[dict[str, Any]],
    bk_cols: Sequence[str],
    existing_fks: Sequence[dict[str, Any]],
    uri: str,
    h_name: str,
    ontology: Any,
    hubs_by_name: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Infer FKs backed by Graph A `canonical_relationships` when CSV metadata omitted them."""
    cbcs = get_cbcs(ontology)
    rels = get_rels(ontology)
    if not cbcs or not rels:
        return list(existing_fks)
    cbc_by_uri = {str(_cbc_field(c, "concept_uri", "")): c for c in cbcs}
    bk_low = {b.lower() for b in bk_cols}
    seen_fk_cols = {
        str(f.get("fk_column") or f.get("source_column") or "").lower()
        for f in existing_fks
        if isinstance(f, dict)
    }
    inferred = list(existing_fks)
    hub_bk_map = {
        hn: {
            str(kc).lower()
            for src in hobj.get("business_key_sources", [])
            for kc in (src.get("source_key_columns") or src.get("key_columns") or [])
        }
        for hn, hobj in (hubs_by_name or {}).items()
    }
    col_list = [str(cd["name"]) for cd in col_descs if isinstance(cd, dict) and cd.get("name")]
    for rel in rels:
        eps = [str(e) for e in (_rel_field(rel, "endpoints", None) or [])]
        if len(eps) != 2 or uri not in eps or eps[0] == eps[1]:
            continue
        tgt_uri = eps[1] if eps[0] == uri else eps[0]
        tgt_cbc = cbc_by_uri.get(tgt_uri)
        if tgt_cbc is None:
            continue
        tgt_hub = str(
            _cbc_field(tgt_cbc, "target_hub", "") or f"hub_{_slug(tgt_uri.rsplit(':', 1)[-1])}"
        )
        tgt_slug = tgt_hub.removeprefix("hub_")
        tgt_attrs, tgt_bks, tgt_tokens = (
            _cbc_attrs(tgt_cbc),
            hub_bk_map.get(tgt_hub, set()),
            set(tgt_slug.split("_")),
        )
        best_col, best_score = None, 0
        for cname in col_list:
            clow = cname.lower()
            if clow in bk_low or clow in seen_fk_cols or clow in _AUDIT_COLS:
                continue
            if not (clow in tgt_attrs or clow in tgt_bks or clow.endswith(_BK_SUFFIXES)):
                continue
            score = (
                (60 if clow in tgt_bks else 0)
                + (40 if clow in tgt_attrs else 0)
                + (
                    35
                    if any(tok and tok in clow for tok in tgt_tokens)
                    and clow.endswith(_BK_SUFFIXES)
                    else 0
                )
            )
            if score >= 70 and score > best_score:
                best_col, best_score = cname, score
        if best_col:
            seen_fk_cols.add(best_col.lower())
            inferred.append(
                {
                    "fk_column": best_col,
                    "target_table": tgt_slug,
                    "target_hub": tgt_hub,
                    "target_concept_uri": tgt_uri,
                    "target_link": _rel_field(rel, "target_link", None),
                    "relationship_uri": _rel_field(rel, "relationship_uri", None),
                }
            )
    return inferred
