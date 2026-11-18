"""STAGE_03/04: Incremental Onboarding (Alg A.7) & Contract-Diff Drift (Alg A.9a).

Conforms to ADR-018, DV-EVO-001..008, and docs/25-data-driven-single-run-pipeline-design.md.
Evolves existing deployed DV 2.0 contracts without requiring baseline_data_path files:
  - Additive companion satellites (sat_*_ext_<epoch>)
  - Staging soft-deprecation projections CAST(NULL AS <type>)
  - Column rename aliases (Levenshtein sim >= 0.85)
  - Records ColumnTriageEntry.source_sql_type on all newly triaged columns.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

from opendv_agent_harness.pipeline.contract_synthesizer import (
    _synthesize_cumulative_contract,
)
from opendv_agent_harness.pipeline.mode_detector import infer_table_action
from opendv_agent_harness.pipeline.ontology_evolution import (
    _bks,
    _make_entry,
    synthesize_ontology_seed_contract,
)
from opendv_agent_harness.pipeline.spec import (
    PipelineUnstructuredDocInput,
    Stage01FeedDiscoveryItem,
)
from opendv_axi_verifier.codemods.alg_a9a_schema_drift import (
    codemod_intra_source_schema_drift,
)
from opendv_core.config import get_core_modeling_config
from opendv_core.models.contract_v5 import NoMistakeDataVault2ContractV5
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    GraphAOntology,
)


def evolve_contract_single_run(
    deployed_contract: NoMistakeDataVault2ContractV5 | dict[str, Any],
    ontology: GraphAOntology,
    discovered_feeds: Sequence[Stage01FeedDiscoveryItem] | Sequence[dict[str, Any]],
    *,
    schema_epoch_tag: str = "v2",
    similarity_threshold: float | None = None,
    cumulative_unstructured_docs: Sequence[Any] | None = None,
    hold_manager: Any | None = None,
    steward_hold_policy: str = "pause",
    run_id: str = "run_default",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Evolve existing deployed contract across incremental and drifted tables.

    Returns:
        (evolved_contract_dict, all_drift_findings)
    """
    core_cfg = get_core_modeling_config()
    sim_thresh = (
        similarity_threshold
        if similarity_threshold is not None
        else core_cfg.levenshtein_rename_threshold
    )
    dep_dict = (
        deployed_contract.model_dump(mode="json")
        if hasattr(deployed_contract, "model_dump")
        else copy.deepcopy(deployed_contract)
    )

    all_drift_findings: list[dict[str, Any]] = []
    incremental_feeds: list[Any] = []
    drifted_feeds: list[Any] = []

    for feed in discovered_feeds:
        if isinstance(feed, Stage01FeedDiscoveryItem):
            ss, tname = feed.source_system, feed.table_name
            cols = [cd.get("name") for cd in feed.column_descriptors] or list(feed.primary_keys)
            types = {
                cd.get("name"): cd.get("physical_type")
                for cd in feed.column_descriptors
                if cd.get("name")
            }
        elif isinstance(feed, dict):
            ss = str(feed.get("source_system") or "source")
            tname = str(feed.get("table_name") or "table")
            cols = list(feed.get("columns") or feed.get("primary_keys") or [])
            types = dict(feed.get("column_types") or {})
        else:
            continue

        action = infer_table_action(
            source_system=ss,
            table_name=tname,
            incoming_columns=cols,
            incoming_types=types,
            contract=dep_dict,
        )

        if action.action == "INTRA_SOURCE_DRIFT":
            drifted_feeds.append(feed)
            all_drift_findings.extend(action.drift_findings)
        elif action.action == "INCREMENTAL_ONBOARDING":
            incremental_feeds.append(feed)

    # 1. First synthesize incremental additions
    if incremental_feeds:
        evolved = _synthesize_cumulative_contract(
            ontology=ontology,
            discovered_feeds=incremental_feeds,
            deployed_contract=dep_dict,
            domain_namespace=dep_dict.get("domain_namespace", "default"),
            is_cold_start=False,
        )
    else:
        evolved = copy.deepcopy(dep_dict)

    # 2. Apply Alg A.9a codemod for drifted feeds
    if drifted_feeds or all_drift_findings:
        # Build candidate satellite payload for codemod
        for feed in drifted_feeds:
            if isinstance(feed, Stage01FeedDiscoveryItem):
                tname = feed.table_name
                descs = feed.column_descriptors
            else:
                tname = str(feed.get("table_name", ""))
                descs = feed.get("column_descriptors") or []

            t_base = tname.split(".")[-1]
            cand_cols = [cd.get("name") for cd in descs]
            cand_types = {
                cd.get("name"): cd.get("physical_type") for cd in descs if cd.get("name")
            }

            # Find matching deployed satellite
            candidate_names = {
                f"sat_{t_base}",
                *(f"sat_{e}_core" for e in core_cfg.core_hub_entities),
            }
            matched_sat = next(
                (
                    s
                    for s in evolved.get("satellites", [])
                    if s.get("source_bronze_table") == tname
                    or s.get("name") in candidate_names
                ),
                None,
            )
            if matched_sat:
                matched_sat["column_types"] = cand_types
                matched_sat["payload_columns"] = cand_cols

        evolved = codemod_intra_source_schema_drift(
            deployed_contract=dep_dict,
            candidate_contract=evolved,
            schema_epoch_tag=schema_epoch_tag,
            similarity_threshold=sim_thresh,
        )

        # Check for KEY_DRIFT findings (ASK-USER) and emit Steward Hold under pause policy
        key_drift_findings = [
            f
            for f in all_drift_findings
            if (
                (
                    isinstance(f, dict)
                    and (f.get("category") == "KEY_DRIFT" or f.get("action") == "ASK-USER")
                )
                or (
                    not isinstance(f, dict)
                    and (
                        getattr(f, "category", None) == "KEY_DRIFT"
                        or getattr(f, "action", None) == "ASK-USER"
                    )
                )
            )
        ]
        if key_drift_findings and hold_manager is not None and steward_hold_policy == "pause":
            for kdf in key_drift_findings:
                col = (
                    kdf.get("column_name")
                    if isinstance(kdf, dict)
                    else getattr(kdf, "column_name", "")
                )
                src = (
                    kdf.get("source_table")
                    if isinstance(kdf, dict)
                    else getattr(kdf, "source_table", "")
                )
                msg = (
                    kdf.get("message")
                    if isinstance(kdf, dict)
                    else getattr(kdf, "message", "Key drift detected")
                )
                hold_manager.record_hold(
                        run_id=run_id,
                        task_id=f"task-drift-key-{col}",
                        session_id=f"sess-drift-{run_id}",
                        stage_id="STAGE_03_SHIP_VAULT_PASS1",
                        persona="ship-vault",
                        headline=f"Key drift on '{src}.{col}': {msg}",
                        escalation_reason="KEY_DRIFT",
                        rule_id="ALG-A9A",
                        options=[
                            {"candidate_id": "reject", "label": "Reject drifted key"},
                            {"candidate_id": "approve", "label": "Approve key migration"},
                        ],
                    )

    if cumulative_unstructured_docs:
        if ontology is not None and hasattr(ontology, "canonical_business_concepts"):
            doc_uri = core_cfg.unstructured_doc_concept_uri
            if not any(
                getattr(c, "concept_uri", None) == doc_uri
                for c in ontology.canonical_business_concepts
            ):
                ontology.canonical_business_concepts.append(
                    CanonicalBusinessConcept(
                        concept_uri=doc_uri,
                        concept_name="Document",
                        target_hub="hub_document",
                        definition="Canonical unstructured document entity.",
                    )
                )

        if not any(h.get("name") == "hub_document" for h in evolved.get("hubs", [])):
            evolved.setdefault("hubs", []).append(
                {
                    "name": "hub_document",
                    "ontology_concept_uri": core_cfg.unstructured_doc_concept_uri,
                    "business_key_sources": [
                        {
                            "bronze_table": core_cfg.unstructured_bronze_table,
                            "key_columns": [core_cfg.unstructured_doc_key_column],
                            "record_source_code": core_cfg.unstructured_record_source,
                            "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                            "require_src_qualifier": False,
                            "bkcc_prefix": "GLOBAL",
                        }
                    ],
                }
            )

        for raw_doc in cumulative_unstructured_docs:
            doc_obj = raw_doc[0] if isinstance(raw_doc, tuple) else raw_doc
            target_hubs: set[str] = set()
            cands = list(getattr(doc_obj, "candidates", []) or []) + list(
                getattr(doc_obj, "extracted_candidates", []) or []
            )
            for cand in cands:
                th = getattr(cand, "target_hub", None) or (
                    cand.get("target_hub") if isinstance(cand, dict) else None
                )
                if th and th != "hub_document":
                    target_hubs.add(th)

            custom_link_name = getattr(doc_obj, "link_name", None) or (
                doc_obj.get("link_name") if isinstance(doc_obj, dict) else None
            )

            if not target_hubs and not custom_link_name:
                available_hubs = [
                    h.get("name")
                    for h in evolved.get("hubs", [])
                    if h.get("name") and h.get("name") != "hub_document"
                ]
                if core_cfg.default_unstructured_target_hub in available_hubs:
                    target_hubs.add(core_cfg.default_unstructured_target_hub)
                elif available_hubs:
                    target_hubs.add(available_hubs[0])
                else:
                    target_hubs.add(core_cfg.default_unstructured_target_hub)

            for th in target_hubs or [core_cfg.default_unstructured_target_hub]:
                th_slug = th.removeprefix("hub_")
                lnk_name = custom_link_name or f"lnk_document_{th_slug}"
                th_hub = next((h for h in evolved.get("hubs", []) if h.get("name") == th), None)
                if th_hub and th_hub.get("business_key_sources"):
                    bks = th_hub["business_key_sources"][0]
                    fk_cols = list(bks.get("key_columns", [])) or [f"{th_slug}_id"]
                else:
                    fk_cols = (
                        [core_cfg.default_customer_tax_id_column]
                        if th == core_cfg.default_unstructured_target_hub
                        else [f"{th_slug}_id"]
                    )
                if not any(lnk.get("name") == lnk_name for lnk in evolved.get("links", [])):
                    rel_uri = f"urn:ontology:relationship:document_{th_slug}_extraction"
                    evolved.setdefault("links", []).append(
                        {
                            "name": lnk_name,
                            "link_type": "UNSTRUCTURED_EXTRACTION_LINK",
                            "ontology_concept_uri": rel_uri,
                            "source_bronze_table": core_cfg.unstructured_bronze_table,
                            "hub_references": [
                                {
                                    "target_hub": "hub_document",
                                    "role_alias": "document",
                                    "source_fk_columns": [core_cfg.unstructured_doc_key_column],
                                },
                                {
                                    "target_hub": th,
                                    "role_alias": th_slug,
                                    "source_fk_columns": fk_cols,
                                },
                            ],
                        }
                    )
                    if (
                        ontology is not None
                        and hasattr(ontology, "canonical_relationships")
                        and not any(
                            getattr(r, "relationship_uri", None) == rel_uri
                            for r in ontology.canonical_relationships
                        )
                    ):
                        th_uri = (
                            th_hub.get("ontology_concept_uri")
                            if th_hub and th_hub.get("ontology_concept_uri")
                            else next(
                                (
                                    c.concept_uri
                                    for c in getattr(
                                        ontology, "canonical_business_concepts", []
                                    )
                                    if getattr(c, "target_hub", None) == th
                                ),
                                f"urn:ontology:entity:{th_slug}",
                            )
                        )
                        ontology.canonical_relationships.append(
                            CanonicalRelationship(
                                relationship_uri=rel_uri,
                                relationship_name=f"Document {th_slug.title()} Extraction",
                                target_link=lnk_name,
                                endpoints=[core_cfg.unstructured_doc_concept_uri, th_uri],
                                grain_semantics=(
                                    "Extraction relationship linking each unstructured "
                                    f"document to resolved entity {th_slug}."
                                ),
                            )
                        )
                prov_sat_name = getattr(
                    doc_obj, "satellite_name", None
                ) or f"sat_{lnk_name.removeprefix('lnk_')}_ai_provenance"
                if not any(
                    s.get("name") == prov_sat_name for s in evolved.get("satellites", [])
                ):
                    evolved.setdefault("satellites", []).append(
                        {
                            "name": prov_sat_name,
                            "satellite_type": "AI_PROVENANCE_SATELLITE",
                            "parent_entity": lnk_name,
                            "source_bronze_table": "unstructured_documents",
                            "security_classification": "INTERNAL",
                            "volatility_tier": "STATIC",
                            "payload_columns": [
                                "confidence_score",
                                "evidence_quote",
                                "extractor_model_id",
                            ],
                            "soft_deprecated_columns": [],
                        }
                    )

        if not any(
            s.get("name") == "mas_document_extractions" for s in evolved.get("satellites", [])
        ):
            evolved.setdefault("satellites", []).append(
                {
                    "name": "mas_document_extractions",
                    "satellite_type": "MULTI_ACTIVE_SATELLITE",
                    "parent_entity": "hub_document",
                    "source_bronze_table": "unstructured_documents",
                    "sub_sequence_column": "extraction_seq",
                    "security_classification": "INTERNAL",
                    "volatility_tier": "STATIC",
                    "payload_columns": ["extraction_field", "extracted_value", "confidence_score"],
                    "soft_deprecated_columns": [],
                }
            )
        if not any(
            e.get("column_name") == "document_id" for e in evolved.get("column_triage_ledger", [])
        ):
            evolved.setdefault("column_triage_ledger", []).append(
                {
                    "bronze_table": "unstructured_documents",
                    "column_name": "document_id",
                    "elm_classification": "BUSINESS_KEY",
                    "target_dv_entity": "hub_document",
                    "source_sql_type": "VARCHAR(64)",
                }
            )

    evolved["lifecycle_stage"] = "MULTI_SOURCE_EXPANSION"
    return evolved, all_drift_findings


def synthesize_unstructured_first_contract(
    ontology: GraphAOntology,
    documents: Sequence[PipelineUnstructuredDocInput],
    domain_namespace: str = "default",
) -> dict[str, Any]:
    """Synthesize Unstructured-First contract."""
    seed = synthesize_ontology_seed_contract(ontology, domain_namespace=domain_namespace)

    core_cfg = get_core_modeling_config()
    has_hub_doc = any(h["name"] == "hub_document" for h in seed["hubs"])
    if not has_hub_doc:
        seed["hubs"].append(
            {
                "name": "hub_document",
                "ontology_concept_uri": core_cfg.unstructured_doc_concept_uri,
                "business_key_sources": [
                    _bks(
                        core_cfg.unstructured_bronze_table,
                        [core_cfg.unstructured_doc_key_column],
                        core_cfg.unstructured_record_source,
                        "TIER_1_GLOBAL_STANDARD",
                        False,
                        "GLOBAL",
                    )
                ],
            }
        )
        seed["satellites"].append(
            {
                "name": core_cfg.unstructured_provenance_sat_name,
                "satellite_type": "AI_PROVENANCE_SATELLITE",
                "parent_entity": "hub_document",
                "source_bronze_table": core_cfg.unstructured_bronze_table,
                "security_classification": "INTERNAL",
                "volatility_tier": "STATIC",
                "payload_columns": list(core_cfg.unstructured_provenance_columns),
                "soft_deprecated_columns": [],
            }
        )
        for col in (
            core_cfg.unstructured_doc_key_column,
            *core_cfg.unstructured_provenance_columns,
        ):
            cls_ = (
                "BUSINESS_KEY"
                if col == core_cfg.unstructured_doc_key_column
                else "DESCRIPTIVE_PAYLOAD"
            )
            tgt = (
                "hub_document"
                if col == core_cfg.unstructured_doc_key_column
                else core_cfg.unstructured_provenance_sat_name
            )
            seed["column_triage_ledger"].append(
                _make_entry(core_cfg.unstructured_bronze_table, col, cls_, tgt)
            )

    return seed


def _add_promo_entities(
    cfg: Any,
    q_tbl: str,
    ss: str,
    hubs: dict[str, Any],
    sats: dict[str, Any],
    lnks: dict[str, Any],
    ledger: list[dict[str, Any]],
) -> None:
    """Synthesize promotion hub, satellite, link, and ledger entries per CoreModelingConfig."""
    h, s, lnk = cfg.promo_hub_name, cfg.promo_sat_name, cfg.promo_link_name
    c = cfg.promo_campaign_column
    if h not in hubs:
        uri = f"urn:ontology:entity:{h.removeprefix('hub_')}"
        hubs[h] = {
            "name": h,
            "ontology_concept_uri": uri,
            "business_key_sources": [_bks(q_tbl, [c], ss)],
        }
        ledger.append(_make_entry(q_tbl, c, "BUSINESS_KEY", h, "VARCHAR(64)"))
    if s not in sats:
        sats[s] = {
            "name": s,
            "satellite_type": "STANDARD_SATELLITE",
            "parent_entity": h,
            "source_bronze_table": q_tbl,
            "security_classification": "INTERNAL",
            "volatility_tier": "STATIC",
            "payload_columns": [c],
            "soft_deprecated_columns": [],
        }
    if lnk not in lnks:
        refs = [
            {"target_hub": f"hub_{r}", "role_alias": r, "source_fk_columns": [col]}
            for r, col in (
                ("order", "order_id"),
                ("customer", cfg.default_customer_tax_id_column),
                ("promotion", c),
            )
        ]
        lnks[lnk] = {
            "name": lnk,
            "link_type": "STANDARD_LINK",
            "ontology_concept_uri": "urn:ontology:relationship:order_promotion",
            "source_bronze_table": q_tbl,
            "hub_references": refs,
            "degenerate_columns": [],
        }


__all__ = [
    "_add_promo_entities",
    "evolve_contract_single_run",
    "synthesize_unstructured_first_contract",
]
