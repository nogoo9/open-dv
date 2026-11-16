"""STAGE_03/04: Greenfield & Seed Data Vault 2.0 Contract Synthesizer."""

from __future__ import annotations

import copy
import re
from collections.abc import Sequence
from typing import Any

from opendv_agent_harness.pipeline.discovery_unstructured import (
    _resolve_hub_and_uri,
    _resolve_relationship_uri,
    _slugify,
)
from opendv_agent_harness.pipeline.ontology_evolution import (
    _bks,
    _make_entry,
    synthesize_ontology_seed_contract,
)
from opendv_agent_harness.pipeline.spec import (
    PipelineSourceFeedInput,
    Stage01FeedDiscoveryItem,
)
from opendv_contracts.errors import OpenDVError
from opendv_core.config import get_core_modeling_config
from opendv_core.models.graph_a import GraphAOntology


def _infer_key_tier(
    cand_cols: Sequence[str],
    ontology: Any,
    uri: str = "",
    h_name: str = "",
    eff_rec_src: str = "SRC",
) -> tuple[str, str]:
    cand_low = [c.lower() for c in cand_cols if c]
    if any(
        any(c.endswith(s) for s in ("lei", "isin", "cusip", "duns", "figi", "sedol", "lei_code"))
        for c in cand_low
    ):
        return "TIER_1_GLOBAL_STANDARD", "GLOBAL"
    if ontology:
        for cbc in getattr(ontology, "canonical_business_concepts", []):
            if (
                (not uri and not h_name)
                or (uri and cbc.concept_uri == uri)
                or (h_name and (cbc.target_hub == h_name or cbc.concept_name in h_name))
            ):
                kat = getattr(cbc, "key_authority_tiers", None)
                if not kat:
                    continue
                for attr, tier in (
                    ("tier_1_global_standard", "TIER_1_GLOBAL_STANDARD"),
                    ("tier_2_enterprise_sor", "TIER_2_ENTERPRISE_SOR"),
                    ("tier_3_local_scoped", "TIER_3_LOCAL_SCOPED"),
                ):
                    t_obj = getattr(kat, attr, None)
                    if t_obj and any(
                        p.lower() in cand_low
                        for p in (getattr(t_obj, "column_patterns", None) or [])
                    ):
                        pol = getattr(t_obj, "bkcc_policy", None)
                        bkcc = (
                            pol
                            if pol
                            and pol.upper()
                            not in {"NONE", "GLOBAL", "SOURCE_SYSTEM_PREFIX_REQUIRED"}
                            else (eff_rec_src if tier == "TIER_3_LOCAL_SCOPED" else "GLOBAL")
                        )
                        return tier, bkcc
    return "TIER_3_LOCAL_SCOPED", eff_rec_src


def _synth_unstructured(docs: Any, domain: str) -> dict[str, Any]:
    doc = docs[0][0] if isinstance(docs[0], tuple) else docs[0]
    rec = getattr(doc, "record_source", "UNSTRUCTURED") or "UNSTRUCTURED"
    b_tbl, bk = "bronze.unstructured_documents", "document_id"
    meta = ["file_name", "sha256", "byte_size", "mime_type", "ingested_at"]
    ledg = [_make_entry(b_tbl, bk, "BUSINESS_KEY", "hub_document", "VARCHAR(64)")]
    ledg.extend(_make_entry(b_tbl, c, "DESCRIPTIVE_PAYLOAD", "sat_document_metadata") for c in meta)
    bks = [_bks(b_tbl, [bk], rec, "TIER_1_GLOBAL_STANDARD", False, "GLOBAL")]
    sat = {
        "name": "sat_document_metadata", "satellite_type": "STANDARD_SATELLITE",
        "parent_entity": "hub_document", "source_bronze_table": b_tbl,
        "security_classification": "INTERNAL", "volatility_tier": "STATIC",
        "payload_columns": meta, "soft_deprecated_columns": [],
    }
    return {
        "contract_version": "5.0", "domain_namespace": domain, "lifecycle_stage": "COLD_START_N1",
        "column_triage_ledger": ledg,
        "hubs": [
            {
                "name": "hub_document",
                "ontology_concept_uri": "urn:ontology:entity:document",
                "business_key_sources": bks,
            }
        ],
        "links": [], "satellites": [sat], "reference_tables": [], "unmapped_escalations": [],
    }


def _synthesize_cumulative_contract(
    ontology: GraphAOntology,
    discovered_feeds: Sequence[Stage01FeedDiscoveryItem] | Sequence[dict[str, Any]] | None = None,
    deployed_contract: dict[str, Any] | None = None,
    domain_namespace: str = "default",
    *,
    is_cold_start: bool = True,
    cumulative_sources: Any = None,
    cumulative_source_systems: Any = None,
    cumulative_unstructured_docs: Any = None,
    added_drift_cols_by_table: Any = None,
    current_wave_drift_tables: Any = None,
    wave_index: int = 0,
    **extra_kwargs: Any,
) -> dict[str, Any]:
    """Synthesize cumulative contract across feeds and ontology."""
    if cumulative_unstructured_docs and not discovered_feeds and not cumulative_sources:
        return _synth_unstructured(cumulative_unstructured_docs, domain_namespace)

    feeds: list[Any] = list(discovered_feeds) if discovered_feeds else []
    if not feeds and cumulative_sources:
        for _q_tbl, src_tuple in cumulative_sources.items():
            src_obj = src_tuple[0] if isinstance(src_tuple, tuple) else src_tuple
            if isinstance(src_obj, PipelineSourceFeedInput):
                fks = [
                    {"fk_column": k, "target_table": v.removeprefix("hub_"), "target_hub": v}
                    for k, v in src_obj.foreign_key_mappings.items()
                ]
                feeds.append(
                    {
                        "source_system": src_obj.source_system,
                        "table_name": src_obj.table_name,
                        "primary_keys": list(src_obj.business_key_columns),
                        "foreign_keys": fks,
                        "key_authority_tier": src_obj.key_authority_tier,
                        "bkcc_namespace": src_obj.bkcc_namespace,
                        "target_hub_name": src_obj.target_hub_name,
                        "record_source_code": src_obj.record_source_code,
                        "column_descriptors": [],
                    }
                )

    if not feeds and not deployed_contract:
        return synthesize_ontology_seed_contract(ontology, domain_namespace=domain_namespace)

    hubs_by_name: dict[str, dict[str, Any]] = {}
    links_by_name: dict[str, dict[str, Any]] = {}
    sats_by_name: dict[str, dict[str, Any]] = {}
    ledger: list[dict[str, Any]] = []

    # Copy deployed entities if expanding incrementally
    if deployed_contract:
        for k, d in (
            ("hubs", hubs_by_name),
            ("links", links_by_name),
            ("satellites", sats_by_name),
        ):
            for x in deployed_contract.get(k, []):
                d[x["name"]] = copy.deepcopy(x)
        for e in deployed_contract.get("column_triage_ledger", []):
            ledger.append(copy.deepcopy(e))

    for feed in feeds:
        if isinstance(feed, Stage01FeedDiscoveryItem):
            ss, tname = feed.source_system, feed.table_name
            pks, fks, col_descs = (
                list(feed.primary_keys),
                list(feed.foreign_keys),
                list(feed.column_descriptors),
            )
        elif isinstance(feed, dict):
            ss, tname = (
                str(feed.get("source_system") or "source"),
                str(feed.get("table_name") or "table"),
            )
            pks = list(feed.get("primary_keys") or [])
            fks = list(feed.get("foreign_keys") or [])
            col_descs = list(feed.get("column_descriptors") or [])
        else:
            continue

        q_table = f"{ss}.{tname}" if ss and ss != "source" else tname
        t_base = tname.split(".")[-1]
        t_slug = _slugify(t_base)

        # 1. Resolve Primary Hub
        target_hub_declared = (
            feed.get("target_hub_name")
            if isinstance(feed, dict)
            else getattr(feed, "target_hub_name", None)
        )
        if target_hub_declared:
            h_name = (
                target_hub_declared
                if target_hub_declared.startswith("hub_")
                else f"hub_{_slugify(target_hub_declared)}"
            )
            try:
                _, uri = _resolve_hub_and_uri(h_name.removeprefix("hub_"), ontology)
            except (KeyError, ValueError, AttributeError, OpenDVError):
                uri = f"urn:ontology:entity:{h_name.removeprefix('hub_')}"
        else:
            try:
                h_name, uri = _resolve_hub_and_uri(t_base, ontology)
            except (KeyError, ValueError, AttributeError, OpenDVError):
                h_name = f"hub_{t_slug}"
                uri = f"urn:ontology:entity:{t_slug}"

        declared_tier = (
            feed.get("key_authority_tier")
            if isinstance(feed, dict)
            else getattr(feed, "key_authority_tier", None)
        )
        raw_bkcc = (
            feed.get("bkcc_namespace") or feed.get("bkcc_prefix")
            if isinstance(feed, dict)
            else getattr(feed, "bkcc_namespace", None)
        )
        eff_rec_src = (
            feed.get("record_source_code")
            if isinstance(feed, dict)
            else getattr(feed, "record_source_code", None)
        ) or (f"{ss.upper()}_{tname.upper()}" if ss else "SRC")

        cand_cols = [
            c.lower() for c in (pks or [cd.get("name") for cd in col_descs if cd.get("name")])
        ]
        inferred_tier, inferred_bkcc = _infer_key_tier(
            cand_cols, ontology, uri, h_name, eff_rec_src
        )
        eff_tier = declared_tier or inferred_tier
        if eff_tier == "TIER_3_LOCAL_SCOPED":
            eff_bkcc = (
                raw_bkcc
                if (
                    raw_bkcc
                    and str(raw_bkcc).upper()
                    not in {"NONE", "GLOBAL", "SOURCE_SYSTEM_PREFIX_REQUIRED"}
                )
                else (ss.upper() if ss and ss != "source" else eff_rec_src)
            )
        elif eff_tier == "TIER_1_GLOBAL_STANDARD":
            eff_bkcc = "GLOBAL"
        else:
            eff_bkcc = raw_bkcc or inferred_bkcc or "GLOBAL"

        bk_cols = list(pks) if pks else []
        if not bk_cols and ontology:
            cnames = {cd.get("name") for cd in col_descs if cd.get("name")}
            for cbc in getattr(ontology, "canonical_business_concepts", []):
                kat = getattr(cbc, "key_authority_tiers", None)
                if (cbc.concept_uri == uri or cbc.target_hub == h_name) and kat:
                    tiers = (
                        kat.tier_1_global_standard,
                        kat.tier_2_enterprise_sor,
                        kat.tier_3_local_scoped,
                    )
                    pats = [
                        p for t in tiers if t for p in (getattr(t, "column_patterns", None) or [])
                    ]
                    m = next((p for p in pats if p in cnames), None)
                    if m:
                        bk_cols = [m]
                        break
        if not bk_cols:
            bk_cols = [f"{t_slug}_id"]

        if ontology:
            for cd in col_descs:
                cname = cd.get("name", "")
                if cname in bk_cols or any(
                    (f.get("fk_column") or f.get("source_column")) == cname for f in fks
                ):
                    continue
                for cbc in getattr(ontology, "canonical_business_concepts", []):
                    if (
                        cbc.concept_uri == uri
                        or cbc.target_hub == h_name
                        or (cbc.target_hub == "hub_document" and cname != "doc_id")
                    ):
                        continue
                    kat = getattr(cbc, "key_authority_tiers", None)
                    if kat:
                        tiers = (
                            kat.tier_1_global_standard,
                            kat.tier_2_enterprise_sor,
                            kat.tier_3_local_scoped,
                        )
                        pats = [
                            p
                            for t in tiers
                            if t
                            for p in (getattr(t, "column_patterns", None) or [])
                        ]
                        if cname in pats:
                            fks.append(
                                {
                                    "fk_column": cname,
                                    "target_table": cbc.target_hub.removeprefix("hub_"),
                                    "target_column": cname,
                                }
                            )
                            break
        is_tier3 = eff_tier == "TIER_3_LOCAL_SCOPED"
        primary_bks = _bks(q_table, bk_cols, eff_rec_src, eff_tier, is_tier3, eff_bkcc)
        if h_name not in hubs_by_name:
            hubs_by_name[h_name] = {
                "name": h_name,
                "ontology_concept_uri": uri,
                "business_key_sources": [primary_bks],
            }
        else:
            hub_srcs = hubs_by_name[h_name]["business_key_sources"]
            if not any(s.get("bronze_table") == q_table for s in hub_srcs):
                hub_srcs.append(primary_bks)

        for bk in bk_cols:
            if not any(
                e.get("bronze_table") == q_table and e.get("column_name") == bk for e in ledger
            ):
                stype = next(
                    (c.get("physical_type") for c in col_descs if c.get("name") == bk),
                    "VARCHAR(64)",
                )
                ledger.append(_make_entry(q_table, bk, "BUSINESS_KEY", h_name, stype))

        # 2. Split Satellites (Alg A.1 3-axis split: standard vs PII/MNPI)
        std_payload: list[str] = []
        pii_payload: list[str] = []
        fk_col_names = {
            (fk.get("fk_column") or fk.get("source_column"))
            for fk in fks
            if (fk.get("fk_column") or fk.get("source_column"))
        }
        surrogate_pattern = re.compile(
            r"(^|_)(sk|seq|row_seq|seq_id|row_id|surrogate|autogen)(_id)?$", re.IGNORECASE
        )

        for cd in col_descs:
            cname = cd.get("name", "")
            if cname in bk_cols or cname in fk_col_names:
                continue
            if surrogate_pattern.search(cname):
                # Monotonic surrogate key quarantine: dropped from payload
                stype = cd.get("physical_type", "BIGINT")
                ledger.append(
                    _make_entry(
                        q_table, cname, "SYSTEM_SURROGATE_DROPPED", "DROPPED_SURROGATE", stype
                    )
                )
                continue
            tags = cd.get("governance_tags") or {}
            is_pii = (
                str(tags.get("classification", "")).lower() in {"pii", "mnpi", "confidential"}
                or str(tags.get("pii", "")).lower() == "true"
                or str(tags.get("mnpi", "")).lower() == "true"
                or any(
                    p in cname.lower()
                    for p in ("email", "ssn", "phone", "tax_id", "salary", "mnpi")
                )
            )
            if is_pii:
                pii_payload.append(cname)
            else:
                std_payload.append(cname)

        cfg = get_core_modeling_config()
        hub_slug = h_name.removeprefix("hub_")
        is_core = hub_slug in cfg.core_hub_entities
        sat_plan = [
            (f"sat_{hub_slug}_core" if is_core else f"sat_{t_slug}", "INTERNAL", std_payload)
        ]
        if pii_payload:
            sat_plan.append(
                (
                    f"sat_{hub_slug}_pii" if is_core else f"sat_{t_slug}_pii",
                    "CONFIDENTIAL",
                    pii_payload,
                )
            )

        for sname, sec, cols in sat_plan:
            if not cols:
                continue
            if sname not in sats_by_name:
                sats_by_name[sname] = {
                    "name": sname,
                    "satellite_type": "STANDARD_SATELLITE",
                    "parent_entity": h_name,
                    "source_bronze_table": q_table,
                    "security_classification": sec,
                    "volatility_tier": "STANDARD",
                    "payload_columns": cols,
                    "soft_deprecated_columns": [],
                }
            else:
                sats_by_name[sname]["payload_columns"] = sorted(
                    set(sats_by_name[sname]["payload_columns"]).union(cols)
                )
            for col in cols:
                if not any(
                    e.get("bronze_table") == q_table and e.get("column_name") == col for e in ledger
                ):
                    stype = next(
                        (c.get("physical_type") for c in col_descs if c.get("name") == col),
                        "VARCHAR(255)",
                    )
                    ledger.append(_make_entry(q_table, col, "DESCRIPTIVE_PAYLOAD", sname, stype))

        # If feed has promo campaign column, synthesize promotion entities per CoreModelingConfig
        if any(c.get("name") == cfg.promo_campaign_column for c in col_descs):
            from opendv_agent_harness.pipeline.contract_evolver import _add_promo_entities

            _add_promo_entities(cfg, q_table, ss, hubs_by_name, sats_by_name, links_by_name, ledger)

        # 3. Foreign Keys -> Links (Alg A.4)
        for fk in fks:
            fk_col = fk.get("fk_column") or fk.get("source_column")
            tgt_t = fk.get("target_table") or fk_col.replace("_id", "") if fk_col else "target"
            if not fk_col:
                continue
            tgt_slug = _slugify(tgt_t)
            try:
                tgt_hub, tgt_uri = _resolve_hub_and_uri(tgt_t, ontology)
            except (KeyError, ValueError, AttributeError, OpenDVError):
                tgt_hub = f"hub_{tgt_slug}"
                tgt_uri = f"urn:ontology:entity:{tgt_slug}"

            if tgt_hub not in hubs_by_name:
                tgt_tier, tgt_bkcc = _infer_key_tier(
                    [fk_col], ontology, tgt_uri, tgt_hub, eff_rec_src
                )
                bks = [
                    _bks(
                        q_table,
                        [fk_col],
                        eff_rec_src,
                        tgt_tier,
                        tgt_tier == "TIER_3_LOCAL_SCOPED",
                        tgt_bkcc,
                    )
                ]
                hubs_by_name[tgt_hub] = {
                    "name": tgt_hub,
                    "ontology_concept_uri": tgt_uri,
                    "business_key_sources": bks,
                }

            hub_slug = h_name.removeprefix("hub_")
            tgt_hub_slug = tgt_hub.removeprefix("hub_")
            lnk_name = f"lnk_{hub_slug}_{tgt_hub_slug}"
            if ontology:
                for rel in getattr(ontology, "canonical_relationships", []):
                    eps = set(rel.endpoints)
                    if (uri in eps and tgt_uri in eps) and rel.target_link:
                        lnk_name = rel.target_link
                        break
            alias_map = dict(cfg.canonical_link_aliases)
            lnk_name = alias_map.get(lnk_name, lnk_name)
            if lnk_name not in links_by_name:
                try:
                    rel_uri = _resolve_relationship_uri(lnk_name, [h_name, tgt_hub], ontology)
                except (KeyError, ValueError, AttributeError, OpenDVError):
                    rel_uri = f"urn:ontology:relationship:{t_slug}_{tgt_slug}"

                refs = [
                    {
                        "target_hub": th,
                        "role_alias": th.removeprefix("hub_"),
                        "source_fk_columns": cs,
                    }
                    for th, cs in ((h_name, bk_cols), (tgt_hub, [fk_col]))
                ]
                links_by_name[lnk_name] = {
                    "name": lnk_name,
                    "link_type": "STANDARD_LINK",
                    "ontology_concept_uri": rel_uri,
                    "source_bronze_table": q_table,
                    "hub_references": refs,
                    "degenerate_columns": [],
                }

            if not any(
                e.get("bronze_table") == q_table
                and e.get("column_name") == fk_col
                and e.get("target_dv_entity") == lnk_name
                for e in ledger
            ):
                stype = next(
                    (c.get("physical_type") for c in col_descs if c.get("name") == fk_col),
                    "VARCHAR(64)",
                )
                ledger.append(_make_entry(q_table, fk_col, "FOREIGN_KEY_REF", lnk_name, stype))

    stage = "COLD_START_N1" if is_cold_start and not deployed_contract else "MULTI_SOURCE_EXPANSION"
    hsc: dict[str, int] = {}
    for s in sats_by_name.values():
        p = s.get("parent_entity", "")
        hsc[p] = hsc.get(p, 0) + 1
    sorted_sats = sorted(
        sats_by_name.values(),
        key=lambda s: (-hsc.get(s.get("parent_entity", ""), 0), s.get("name", "")),
    )
    return {
        "contract_version": "5.0",
        "domain_namespace": domain_namespace,
        "lifecycle_stage": stage,
        "column_triage_ledger": ledger,
        "hubs": list(hubs_by_name.values()),
        "links": list(links_by_name.values()),
        "satellites": sorted_sats,
        "reference_tables": [],
        "unmapped_escalations": [],
    }


__all__ = [
    "_synthesize_cumulative_contract",
    "synthesize_ontology_seed_contract",
]
