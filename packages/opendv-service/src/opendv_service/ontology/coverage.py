"""Ontology Coverage Calculation Engine (Decision D9, ONT-03).

Computes deterministic coverage between Graph A Canonical Business Concepts and
incoming Bronze relational sources, profiles, and unstructured candidates.
Replaces silent provisional-URI synthesis with explicit gap reporting.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opendv_contracts.dtos.coverage import (
    MappedConceptCoverage,
    OntologyCoverageArtifact,
    UnmappedColumnGap,
    UnmappedMentionGap,
    UnmappedTableGap,
)
from opendv_core.models.graph_a import CanonicalBusinessConcept, GraphAOntology


def _slugify(val: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", val.strip()).strip("_").lower()
    return cleaned or "default"


def _tokenize(text: str) -> set[str]:
    raw_toks = re.split(r"[^a-z0-9]+", text.lower())
    return {t.rstrip("s") for t in raw_toks if len(t.rstrip("s")) >= 2}


def _normalize_ontology_dict(raw: dict[str, Any]) -> dict[str, Any]:
    norm = dict(raw)
    if "ontology_version" not in norm and "version" in norm:
        norm["ontology_version"] = str(norm["version"])
    if "domain_namespace" not in norm and "ontology_id" in norm:
        norm["domain_namespace"] = _slugify(str(norm["ontology_id"]))
    elif "domain_namespace" not in norm:
        norm["domain_namespace"] = "general"
    if "enterprise_owner" not in norm:
        norm["enterprise_owner"] = "Enterprise Data Steward"
    cbcs = []
    for c in norm.get("canonical_business_concepts", []):
        if isinstance(c, dict):
            c_norm = dict(c)
            if "concept_name" not in c_norm and "label" in c_norm:
                c_norm["concept_name"] = str(c_norm["label"])
            elif "concept_name" not in c_norm and "concept_id" in c_norm:
                c_norm["concept_name"] = str(c_norm["concept_id"])
            if "elm_archetype" not in c_norm:
                c_norm["elm_archetype"] = str(
                    c_norm.get("taxonomy_class", "CORE_BUSINESS_ENTITY")
                )
            if "definition" not in c_norm:
                ent_name = c_norm.get("concept_name", "entity")
                c_norm["definition"] = f"Canonical definition for {ent_name}"
            if "concept_uri" in c_norm and ":provisional:" in str(c_norm["concept_uri"]):
                c_norm["concept_uri"] = str(c_norm["concept_uri"]).replace(":provisional:", ":")
            valid_keys = {
                "concept_uri",
                "concept_name",
                "elm_archetype",
                "definition",
                "target_hub",
                "governance_constraints",
                "key_authority_tiers",
            }
            clean_c = {k: v for k, v in c_norm.items() if k in valid_keys}
            cbcs.append(clean_c)
    norm["canonical_business_concepts"] = cbcs
    valid_root_keys = {
        "ontology_version",
        "parent_sha256",
        "decision_refs",
        "domain_namespace",
        "enterprise_owner",
        "standards_references",
        "canonical_business_concepts",
        "canonical_relationships",
        "reference_enumerations",
    }
    return {k: v for k, v in norm.items() if k in valid_root_keys}


def _resolve_graph_a(graph_a: Any) -> tuple[GraphAOntology, str]:
    """Parse or validate Graph A and compute canonical SHA-256."""
    if isinstance(graph_a, GraphAOntology):
        ont_obj = graph_a
        raw_bytes = json.dumps(
            ont_obj.model_dump(mode="json", exclude_none=True), sort_keys=True
        ).encode("utf-8")
        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        return ont_obj, sha256

    if isinstance(graph_a, dict):
        try:
            ont_obj = GraphAOntology.model_validate(graph_a)
        except (ValueError, TypeError):
            ont_obj = GraphAOntology.model_validate(_normalize_ontology_dict(graph_a))
        raw_bytes = json.dumps(graph_a, sort_keys=True).encode("utf-8")
        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        return ont_obj, sha256

    if isinstance(graph_a, (str, Path)):
        p = Path(graph_a)
        content = p.read_text(encoding="utf-8") if p.is_file() else str(graph_a)
        try:
            import yaml

            parsed = yaml.safe_load(content) or {}
        except (yaml.YAMLError, ValueError):
            parsed = json.loads(content)
        try:
            ont_obj = GraphAOntology.model_validate(parsed)
        except (ValueError, TypeError):
            ont_obj = GraphAOntology.model_validate(_normalize_ontology_dict(parsed))
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return ont_obj, sha256

    raise TypeError(f"Unsupported Graph A input type: {type(graph_a)}")


def compute_ontology_coverage(
    graph_a: GraphAOntology | dict[str, Any] | str | Path,
    bronze_manifest: Any = None,
    profiles: Any = None,
    contract: Any = None,
    unstructured_docs: Any = None,
    wave_id: str = "wave_0",
    run_id: str | None = None,
) -> OntologyCoverageArtifact:
    """Compute explicit deterministic ontology coverage metrics and gap reports.

    Args:
        graph_a: GraphAOntology instance, dict, or file path.
        bronze_manifest: PipelineWaveInput, list of feeds, or raw source manifest.
        profiles: GraphBProfile or dict of profiled tables/columns.
        contract: Optional candidate or deployed contract dict / model.
        unstructured_docs: Optional list of unstructured document inputs.
        wave_id: Pipeline wave identifier.
        run_id: Optional pipeline run identifier.

    Returns:
        OntologyCoverageArtifact reporting coverage_ratio, gaps, and diagnostics.
    """
    ontology, graph_a_sha256 = _resolve_graph_a(graph_a)
    diagnostics: list[str] = []
    provisional_uris_found: list[str] = []

    # 1. Index Graph A concepts and check for deprecated provisional URIs
    raw_graph_a_str = ""
    if isinstance(graph_a, (str, Path)):
        p = Path(graph_a)
        raw_graph_a_str = p.read_text(encoding="utf-8") if p.is_file() else str(graph_a)
    elif isinstance(graph_a, dict):
        raw_graph_a_str = json.dumps(graph_a)
    if raw_graph_a_str:
        for prov_match in re.findall(r"urn:ontology:provisional:[a-zA-Z0-9_:]+", raw_graph_a_str):
            if prov_match not in provisional_uris_found:
                provisional_uris_found.append(prov_match)
                diagnostics.append(
                    f"ONT-PROV-URI: Deprecated provisional URI '{prov_match}' found in Graph A; "
                    "provisional URIs are forbidden by Decision D9."
                )

    known_cbc_by_uri: dict[str, CanonicalBusinessConcept] = {}
    known_cbc_by_hub: dict[str, CanonicalBusinessConcept] = {}
    known_cbc_by_name: dict[str, CanonicalBusinessConcept] = {}
    known_col_patterns: dict[str, CanonicalBusinessConcept] = {}

    for cbc in ontology.canonical_business_concepts:
        uri = cbc.concept_uri
        if ":provisional:" in uri:
            provisional_uris_found.append(uri)
            diagnostics.append(
                f"ONT-PROV-URI: Deprecated provisional URI '{uri}' found in Graph A; "
                "provisional URIs are forbidden by Decision D9."
            )
        known_cbc_by_uri[uri.lower()] = cbc
        known_cbc_by_name[cbc.concept_name.lower()] = cbc
        if cbc.target_hub:
            known_cbc_by_hub[cbc.target_hub.lower()] = cbc

        if cbc.key_authority_tiers:
            for t_spec in (
                cbc.key_authority_tiers.tier_1_global_standard,
                cbc.key_authority_tiers.tier_2_enterprise_sor,
                cbc.key_authority_tiers.tier_3_local_scoped,
            ):
                if t_spec and t_spec.column_patterns:
                    for pat in t_spec.column_patterns:
                        known_col_patterns[pat.lower()] = cbc

    # 2. Survey all Bronze tables and columns across inputs
    surveyed_tables: dict[str, dict[str, Any]] = {}

    # 2a. From profiles (GraphBProfile or dict)
    if profiles is not None:
        p_dict = (
            profiles.model_dump(mode="json")
            if hasattr(profiles, "model_dump")
            else dict(profiles)
        )
        for sys_obj in p_dict.get("source_systems", []):
            if isinstance(sys_obj, dict):
                for tbl_obj in sys_obj.get("tables", []):
                    if isinstance(tbl_obj, dict):
                        b_tbl = str(tbl_obj.get("bronze_table", ""))
                        if not b_tbl:
                            continue
                        cols = [
                            str(c.get("column_name"))
                            for c in tbl_obj.get("columns", [])
                            if isinstance(c, dict) and "column_name" in c
                        ]
                        col_types = {
                            str(c.get("column_name")): str(c.get("data_type", "VARCHAR"))
                            for c in tbl_obj.get("columns", [])
                            if isinstance(c, dict) and "column_name" in c
                        }
                        surveyed_tables[b_tbl] = {
                            "table_name": b_tbl,
                            "columns": cols,
                            "col_types": col_types,
                            "row_count": tbl_obj.get("row_count"),
                            "hints": [],
                        }

    # 2b. From bronze_manifest (PipelineWaveInput or source feeds)
    sources = []
    if bronze_manifest is not None:
        if hasattr(bronze_manifest, "sources"):
            sources = list(bronze_manifest.sources)
        elif isinstance(bronze_manifest, dict) and "sources" in bronze_manifest:
            sources = list(bronze_manifest["sources"])
        elif isinstance(bronze_manifest, list):
            sources = list(bronze_manifest)

    for src in sources:
        s_dict = (
            src.model_dump(mode="json") if hasattr(src, "model_dump") else dict(src)
        )
        sys_code = str(s_dict.get("source_system", "bronze")).lower()
        tbl_name = str(s_dict.get("table_name", "")).lower()
        q_tbl = f"{sys_code}.{tbl_name}" if sys_code and tbl_name else (tbl_name or "unknown")

        hints = []
        if s_dict.get("target_hub_name"):
            hints.append(str(s_dict["target_hub_name"]))
        hints.extend(list(s_dict.get("cbc_mapping_hints", {}).values()))

        cols = list(s_dict.get("business_key_columns", []))
        cols.extend(list(s_dict.get("foreign_key_mappings", {}).keys()))
        cols.extend(list(s_dict.get("surrogate_key_columns", [])) )
        cols.extend(list(s_dict.get("pii_columns", [])))

        if q_tbl in surveyed_tables:
            surveyed_tables[q_tbl]["hints"].extend(hints)
            existing_cols = surveyed_tables[q_tbl]["columns"]
            for c in cols:
                if c not in existing_cols:
                    existing_cols.append(c)
            if not cols and s_dict.get("columns"):
                cols = [str(c) for c in s_dict["columns"]]
            surveyed_tables[q_tbl] = {
                "table_name": q_tbl,
                "columns": cols,
                "col_types": {},
                "row_count": None,
                "hints": hints,
            }

    # 2c. From contract if provided
    contract_dict = None
    if contract is not None:
        if isinstance(contract, (str, Path)):
            p = Path(contract)
            raw_c = p.read_text(encoding="utf-8") if p.is_file() else str(contract)
            try:
                import yaml

                contract_dict = yaml.safe_load(raw_c) or {}
            except (yaml.YAMLError, ValueError):
                contract_dict = json.loads(raw_c)
        elif hasattr(contract, "model_dump"):
            contract_dict = contract.model_dump(mode="json")
        else:
            contract_dict = dict(contract)
        for h in contract_dict.get("hubs", []):
            if isinstance(h, dict):
                h_uri = str(h.get("ontology_concept_uri", ""))
                if ":provisional:" in h_uri and h_uri not in provisional_uris_found:
                    provisional_uris_found.append(h_uri)
                    diagnostics.append(
                        f"ONT-PROV-URI: Deprecated provisional URI '{h_uri}' found on hub "
                        f"'{h.get('name')}'; provisional URIs are forbidden."
                    )
                for bks in h.get("business_key_sources", []):
                    if isinstance(bks, dict) and "bronze_table" in bks:
                        b_tbl = str(bks["bronze_table"])
                        k_cols = [str(k) for k in bks.get("key_columns", [])]
                        if b_tbl not in surveyed_tables:
                            surveyed_tables[b_tbl] = {
                                "table_name": b_tbl,
                                "columns": k_cols,
                                "col_types": {},
                                "row_count": None,
                                "hints": [str(h.get("name", ""))],
                            }
                        else:
                            for kc in k_cols:
                                if kc not in surveyed_tables[b_tbl]["columns"]:
                                    surveyed_tables[b_tbl]["columns"].append(kc)

    # 3. Match surveyed tables and columns against Graph A CBCs
    mapped_concepts: set[str] = set()
    mapped_tables: list[str] = []
    unmapped_tables: list[str] = []
    unmapped_columns: list[dict[str, str]] = []

    detailed_mapped: list[MappedConceptCoverage] = []
    detailed_unmapped_tables: list[UnmappedTableGap] = []
    detailed_unmapped_columns: list[UnmappedColumnGap] = []

    for t_name, t_info in surveyed_tables.items():
        cols = t_info["columns"]
        hints = t_info["hints"]
        tbl_tokens = _tokenize(t_name.rsplit(".", 1)[-1])

        matched_cbc: CanonicalBusinessConcept | None = None
        unmapped_reason = "ABSENT_FROM_ONTOLOGY"

        # Check explicit contract mapping first
        if contract_dict is not None:
            for h in contract_dict.get("hubs", []):
                if isinstance(h, dict):
                    h_bks = [
                        str(bks.get("bronze_table", ""))
                        for bks in h.get("business_key_sources", [])
                        if isinstance(bks, dict)
                    ]
                    if (
                        t_name in h_bks
                        or t_name.rsplit(".", 1)[-1] in [b.rsplit(".", 1)[-1] for b in h_bks]
                    ):
                        h_uri = str(h.get("ontology_concept_uri", ""))
                        if h_uri.lower() in known_cbc_by_uri:
                            matched_cbc = known_cbc_by_uri[h_uri.lower()]
                        else:
                            unmapped_reason = (
                                f"Hub '{h.get('name')}' references unknown URI '{h_uri}'"
                            )
                        break

        # Check explicit hints
        if matched_cbc is None:
            for hint in hints:
                h_low = hint.lower()
                if h_low in known_cbc_by_hub:
                    matched_cbc = known_cbc_by_hub[h_low]
                    break
                if h_low in known_cbc_by_name:
                    matched_cbc = known_cbc_by_name[h_low]
                    break
                if h_low in known_cbc_by_uri:
                    matched_cbc = known_cbc_by_uri[h_low]
                    break

        # Check token matching against CBC names / hubs
        if matched_cbc is None:
            for cbc in ontology.canonical_business_concepts:
                c_toks = _tokenize(cbc.concept_name)
                if cbc.target_hub:
                    c_toks |= _tokenize(cbc.target_hub)
                if tbl_tokens and (tbl_tokens & c_toks):
                    matched_cbc = cbc
                    break

        # Check column patterns
        if matched_cbc is None:
            for col in cols:
                c_low = col.lower()
                if c_low in known_col_patterns:
                    matched_cbc = known_col_patterns[c_low]
                    break

        if matched_cbc is not None:
            mapped_tables.append(t_name)
            mapped_concepts.add(matched_cbc.concept_uri)
            mapped_cols_entries = [{"table": t_name, "column": c} for c in cols]
            detailed_mapped.append(
                MappedConceptCoverage(
                    concept_uri=matched_cbc.concept_uri,
                    concept_name=matched_cbc.concept_name,
                    target_hub=matched_cbc.target_hub,
                    mapped_tables=[t_name],
                    mapped_columns=mapped_cols_entries,
                    confidence=1.0,
                )
            )
        else:
            unmapped_tables.append(t_name)
            detailed_unmapped_tables.append(
                UnmappedTableGap(
                    table_name=t_name,
                    columns=cols,
                    row_count=t_info.get("row_count"),
                    candidate_hints=hints,
                    reason=unmapped_reason,
                )
            )
            diagnostics.append(
                f"ONT-COV-GAP: Table '{t_name}' has no matching Canonical Business "
                f"Concept in Graph A ({unmapped_reason})."
            )
            for c in cols:
                unmapped_columns.append({"table": t_name, "column": c})
                detailed_unmapped_columns.append(
                    UnmappedColumnGap(
                        table_name=t_name,
                        column_name=c,
                        inferred_type=t_info["col_types"].get(c),
                        candidate_concept_uris=[],
                    )
                )

    # 4. Check unstructured mentions
    unmapped_mentions: list[str] = []
    detailed_unmapped_mentions: list[UnmappedMentionGap] = []
    unstr_list = []
    if unstructured_docs:
        unstr_list.extend(unstructured_docs)
    elif bronze_manifest and hasattr(bronze_manifest, "unstructured_documents"):
        unstr_list.extend(bronze_manifest.unstructured_documents)

    for doc in unstr_list:
        doc_d = (
            doc.model_dump(mode="json") if hasattr(doc, "model_dump") else dict(doc)
        )
        doc_sha = str(doc_d.get("doc_sha256") or doc_d.get("sha256") or "unknown")
        for cand in doc_d.get("extracted_candidates", []):
            if isinstance(cand, dict):
                t_hub = str(cand.get("target_hub", "")).lower()
                if t_hub and t_hub not in known_cbc_by_hub and t_hub != "hub_document":
                    quote = str(cand.get("verbatim_quote", ""))
                    unmapped_mentions.append(quote)
                    detailed_unmapped_mentions.append(
                        UnmappedMentionGap(
                            doc_sha256=doc_sha,
                            char_start=int(cand.get("char_start", 0)),
                            char_end=int(cand.get("char_end", 0)),
                            text_snippet=quote,
                            inferred_entity_type=t_hub,
                        )
                    )
                    diagnostics.append(
                        f"ONT-COV-GAP: Document mention '{quote}' targets unknown "
                        f"Hub '{t_hub}' absent from Graph A."
                    )

    # 5. Compute summary metrics and coverage ratio
    total_tables = len(surveyed_tables)
    mapped_tables_count = len(mapped_tables)
    unmapped_tables_count = len(unmapped_tables)

    total_columns = sum(len(t_info["columns"]) for t_info in surveyed_tables.values())
    unmapped_columns_count = len(unmapped_columns)
    mapped_columns_count = max(0, total_columns - unmapped_columns_count)

    coverage_ratio = (
        1.0
        if total_columns == 0
        else round(float(mapped_columns_count) / float(max(1, total_columns)), 4)
    )

    norm_wave_id = (
        wave_id
        if wave_id.startswith("wave_") or wave_id.startswith("w")
        else f"wave_{wave_id}"
    )
    art_id = f"art-{norm_wave_id}-stage01-ontology-coverage"

    return OntologyCoverageArtifact(
        artifact_id=art_id,
        run_id=run_id,
        wave_id=norm_wave_id,
        domain_namespace=ontology.domain_namespace,
        graph_a_version=getattr(ontology, "ontology_version", "1.0.0"),
        graph_a_sha256=graph_a_sha256,
        total_tables=total_tables,
        mapped_tables_count=mapped_tables_count,
        unmapped_tables_count=unmapped_tables_count,
        total_columns=total_columns,
        mapped_columns_count=mapped_columns_count,
        unmapped_columns_count=unmapped_columns_count,
        coverage_ratio=coverage_ratio,
        mapped_concepts=sorted(mapped_concepts),
        mapped_tables=sorted(mapped_tables),
        unmapped_tables=sorted(unmapped_tables),
        unmapped_columns=unmapped_columns,
        unmapped_mentions=unmapped_mentions,
        provisional_uris_found=provisional_uris_found,
        diagnostics=diagnostics,
        detailed_mapped_concepts=detailed_mapped,
        detailed_unmapped_tables=detailed_unmapped_tables,
        detailed_unmapped_columns=detailed_unmapped_columns,
        detailed_unmapped_mentions=detailed_unmapped_mentions,
        generated_at_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
