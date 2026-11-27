"""Step 4 source/document context assembly, prompt formatting, and raw delta normalization."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from opendv.grounding.providers.fibo.loader import get_bundled_fibo_pack_sha256
from opendv.models.ontology import GraphAOntology
from opendv.models.project import ConceptMemorySpec
from opendv.models.sources import DocumentMap, SourceProfile
from opendv.models.vault_ir import map_sql_to_portable_type
from opendv.models.wire_dtos_discovery import ConceptSummary
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._project_seeds import discover_workspace_seeds
from opendv.service.workflows._source_docs import resolve_readable_source_path
from opendv.sources import DocumentMapBuilder

_ALLOWED_ATTR_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "logical_type",
        "sql_type_hint",
        "nullable",
        "is_business_key",
        "is_pii",
        "pii_category",
        "enum_uri",
        "standard_property_uri",
    }
)


_KEY_TIER_ALIASES: dict[str, str] = {
    "TIER_3_ENTERPRISE_STANDARD": "TIER_3_LOCAL_SCOPED",
    "TIER_2_INDUSTRY_CONSORTIUM": "TIER_2_ENTERPRISE_SOR",
}


def _normalize_attr_dict(raw_attr: dict[str, Any]) -> dict[str, Any]:
    a = {k: v for k, v in raw_attr.items() if k in _ALLOWED_ATTR_KEYS}
    if "name" not in a and "attribute_name" in raw_attr:
        a["name"] = raw_attr["attribute_name"]
    sql_hint = str(a.get("sql_type_hint") or raw_attr.get("data_type") or "VARCHAR")
    a["sql_type_hint"] = sql_hint
    raw_logical = a.get("logical_type") or sql_hint
    a["logical_type"] = normalize_logical_type(raw_logical)
    is_bk = bool(a.get("is_business_key", raw_attr.get("is_primary_key", False)))
    a["is_business_key"] = is_bk
    a.setdefault("nullable", not is_bk)
    a.setdefault("is_pii", False)
    return a


def _normalize_relationship_dict(rel: dict[str, Any], *, fallback_desc: str = "") -> dict[str, Any]:
    name = str(rel.get("relationship_name") or "relates_to")
    uri = str(rel.get("relationship_uri") or f"urn:opendv:rel:{name}")
    src_uri = str(rel.get("source_concept_uri") or rel.get("from_concept_uri") or "")
    dst_uri = str(rel.get("target_concept_uri") or rel.get("to_concept_uri") or "")
    desc = str(rel.get("description") if rel.get("description") is not None else fallback_desc)
    slug = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_") or "rel"
    target_link = str(rel.get("target_link") or f"lnk_{slug}")
    if not target_link.startswith(("lnk_", "t_lnk_", "sal_", "hal_")):
        target_link = f"lnk_{target_link}"
    grain = str(rel.get("grain_semantics") or desc or "Per source-target link grain")
    raw_link_attrs = rel.get("link_attributes")
    link_attrs = (
        [_normalize_attr_dict(a) for a in raw_link_attrs if isinstance(a, dict)]
        if isinstance(raw_link_attrs, list)
        else []
    )
    out: dict[str, Any] = {
        "relationship_uri": uri,
        "relationship_name": name,
        "source_concept_uri": src_uri,
        "target_concept_uri": dst_uri,
        "cardinality": str(rel.get("cardinality") or "ONE_TO_MANY"),
        "target_link": target_link,
        "grain_semantics": grain,
        "description": desc,
        "link_attributes": link_attrs,
    }
    for opt_key in (
        "standard_property_uri",
        "role_uri",
        "played_by_uri",
        "source_table_id",
        "source_col_name",
        "target_col_name",
        "source_anchor_col_name",
    ):
        if rel.get(opt_key) is not None:
            out[opt_key] = rel[opt_key]
    return out


def normalize_logical_type(raw_val: Any) -> Any:
    """Map SQL/LLM type shorthands onto PortableLogicalType via precision-safe mapper."""
    if isinstance(raw_val, str):
        mapped = map_sql_to_portable_type(raw_val)
        return mapped.model_dump(mode="json") if isinstance(mapped, BaseModel) else mapped
    return raw_val


def normalize_step4_raw_op(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize minor LLM JSON omissions on Step 4 additive op dicts before strict validation."""
    op = dict(raw["op"]) if isinstance(raw.get("op"), dict) else dict(raw)
    if "op_type" not in op:
        alias = op.pop("type", None) or op.pop("operation", None)
        if isinstance(alias, str) and alias:
            op["op_type"] = alias
    if isinstance(op.get("external_mappings"), list):
        fixed_maps = []
        for item in op["external_mappings"]:
            if isinstance(item, dict):
                m = dict(item)
                m.setdefault("reference_pack_id", "fibo-core")
                m.setdefault("reference_pack_sha256", get_bundled_fibo_pack_sha256())
                fixed_maps.append(m)
        op["external_mappings"] = fixed_maps
    if isinstance(op.get("mapping"), dict):
        m = dict(op["mapping"])
        m.setdefault("reference_pack_id", "fibo-core")
        m.setdefault("reference_pack_sha256", get_bundled_fibo_pack_sha256())
        op["mapping"] = m
    if isinstance(op.get("attributes"), list):
        op["attributes"] = [
            _normalize_attr_dict(item) for item in op["attributes"] if isinstance(item, dict)
        ]
    if op.get("op_type") == "AddAttribute" and "attribute" not in op:
        flat_attr: dict[str, Any] = {
            "name": str(op.pop("attribute_name", None) or op.pop("name", None) or "attr"),
            "sql_type_hint": str(
                op.pop("data_type", None) or op.pop("sql_type_hint", None) or "VARCHAR"
            ),
            "is_business_key": bool(op.pop("is_primary_key", op.pop("is_business_key", False))),
        }
        for k in (
            "nullable", "is_pii", "logical_type",
            "pii_category", "enum_uri", "standard_property_uri",
        ):
            if k in op:
                flat_attr[k] = op.pop(k)
        op.pop("description", None)
        op.pop("table_bindings", None)
        op["attribute"] = _normalize_attr_dict(flat_attr)
    elif op.get("op_type") == "AddAttribute" and isinstance(op.get("attribute"), dict):
        op["attribute"] = _normalize_attr_dict(dict(op["attribute"]))
    elif op.get("op_type") == "AddRelationship" and "relationship" not in op:
        rel_fields = {
            k: op.pop(k)
            for k in (
                "relationship_uri",
                "relationship_name",
                "source_concept_uri",
                "from_concept_uri",
                "target_concept_uri",
                "to_concept_uri",
                "cardinality",
                "target_link",
                "grain_semantics",
                "standard_property_uri",
                "role_uri",
                "played_by_uri",
                "source_table_id",
                "source_col_name",
                "target_col_name",
                "source_anchor_col_name",
                "link_attributes",
            )
            if k in op
        }
        desc_fallback = str(op.pop("description", "") or "")
        op["relationship"] = _normalize_relationship_dict(rel_fields, fallback_desc=desc_fallback)
    elif op.get("op_type") == "AddRelationship" and isinstance(op.get("relationship"), dict):
        op["relationship"] = _normalize_relationship_dict(dict(op["relationship"]))
    if isinstance(op.get("key_authority_tier"), str):
        tier_up = op["key_authority_tier"].strip().upper()
        if tier_up in _KEY_TIER_ALIASES:
            op["key_authority_tier"] = _KEY_TIER_ALIASES[tier_up]
    return op


def build_source_and_doc_context(
    state_store: ProjectStateManager,
    project_id: str,
    workspace_root: Path,
) -> tuple[list[SourceProfile], dict[str, list[str]], list[str]]:
    """Collect structured SourceProfiles and scan-gated DocumentMap page texts."""
    profiles: list[SourceProfile] = []
    parsed_pages: dict[str, list[str]] = {}
    doc_summaries: list[str] = []

    for src in state_store.list_sources(project_id):
        rec = state_store.get_source_record(project_id, src.source_id)
        prof = SourceProfile.model_validate(rec["profile"])
        if prof.tables:
            profiles.append(prof)
        if rec["document_map"] is None or str(rec["status"]) == "METADATA_ONLY_BINARY_ABSENT":
            continue
        dmap = DocumentMap.model_validate(rec["document_map"])
        approved = {int(p) for p in rec["approved_scan_pages"]} | set(dmap.approved_scan_pages)
        disk_path = resolve_readable_source_path(state_store, workspace_root, project_id, rec)
        if disk_path is None:
            continue
        rebuilt, pages = DocumentMapBuilder().build_from_path(
            source_id=src.source_id,
            path=disk_path,
            doc_format=dmap.format,
            approved_scan_pages=sorted(approved),
        )
        safe_pages: list[str] = []
        for idx, page_meta in enumerate(rebuilt.pages):
            if page_meta.is_scanned_or_low_density and page_meta.page_number not in approved:
                safe_pages.append("")
            else:
                safe_pages.append(pages[idx] if idx < len(pages) else "")
        parsed_pages[src.source_id] = safe_pages
        parsed_pages[dmap.doc_id] = safe_pages
        doc_summaries.append(
            f"- doc_id={dmap.doc_id} (source={src.source_id}, file={dmap.filename}, "
            f"pages={dmap.page_count})"
        )
    return profiles, parsed_pages, doc_summaries


def compute_profiles_fp(profiles: list[SourceProfile]) -> str:
    """Compute a deterministic short fingerprint across attached SourceProfiles."""
    if not profiles:
        return "fp-empty"
    raw = "|".join(f"{p.source_id}:{p.schema_fingerprint}" for p in profiles)
    return f"fp-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def build_workspace_seed_concepts(
    state_store: ProjectStateManager,
    project_id: str,
    workspace_root: Path,
) -> list[ConceptSummary]:
    """Assemble ConceptSummary entries from workspace seeds and persisted concept memory."""
    out: list[ConceptSummary] = []
    for seed, _ont in discover_workspace_seeds(workspace_root):
        out.append(
            ConceptSummary(
                concept_uri=f"seed:{seed.seed_id}",
                concept_name=seed.domain_namespace,
                source_layer="workspace_seed",
                locked_in_vault=False,
            )
        )
    mem_revs = state_store.list_revisions(project_id, rev_kind="concept_memory")
    for row in mem_revs:
        mem = ConceptMemorySpec.model_validate(row["payload"])
        if mem.concept_uri:
            out.append(
                ConceptSummary(
                    concept_uri=mem.concept_uri,
                    concept_name=mem.term,
                    source_layer="concept_memory",
                    locked_in_vault=False,
                )
            )
    return out


def format_step4_prompt(
    *,
    project_id: str,
    ontology: GraphAOntology,
    profiles: list[SourceProfile],
    doc_summaries: list[str],
    discovery_mode: str,
    hint: str | None,
    answers: dict[str, str] | None = None,
) -> str:
    """Build compact stateless Step 4 coordinator prompt."""
    table_lines: list[str] = []
    for prof in profiles:
        for tbl in prof.tables:
            cols = ", ".join(
                f"{c.col_name}:{c.sql_type}{'(PK)' if c.is_pk else ''}"
                for c in tbl.columns[:12]
            )
            table_lines.append(
                f"- source_id={prof.source_id} table_id={tbl.table_id} "
                f"({tbl.row_count_est} rows): [{cols}]"
            )
    existing = ", ".join(c.concept_name for c in ontology.concepts[:25]) or "none"
    parts = [
        f"Project: {project_id} | Domain namespace: {ontology.domain_namespace} | "
        f"Ontology v{ontology.ontology_version} | Mode: {discovery_mode}",
        f"Existing concepts: {existing}",
        "Structured tables:\n" + ("\n".join(table_lines) if table_lines else "- none"),
        "Documents:\n" + ("\n".join(doc_summaries) if doc_summaries else "- none"),
        "INSTRUCTIONS: All available source schemas and documents are supplied above. "
        "Do NOT call local filesystem or shell tools (`read_file`, `run_command`, etc.). "
        "If MCP Coordinator tools (`mcp__opendv_broker__*`) are attached, you may use them; "
        "otherwise propose additive Graph A deltas directly inside JSON matching "
        "Step4CoordinatorOutput (`proposed_ops`, `clarification_questions`, `summary`) "
        "and finish immediately without committing Graph A. Every item in `proposed_ops` MUST "
        'include `"op_type"` (`AddConcept` with inline `attributes`/`table_bindings` and valid '
        '`key_authority_tier` in `TIER_1_GLOBAL_STANDARD | TIER_2_ENTERPRISE_SOR | '
        'TIER_3_LOCAL_SCOPED`, `AddAttribute` with nested `"attribute": {"name", "logical_type", '
        '"sql_type_hint", "nullable", "is_business_key", "is_pii"}`, `AddRelationship` with nested '
        '`"relationship": {"relationship_uri": "urn:...", "relationship_name": "...", '
        '"source_concept_uri": "urn:...", "target_concept_uri": "urn:...", "cardinality": '
        '"ONE_TO_ONE|ONE_TO_MANY|MANY_TO_ONE|MANY_TO_MANY", "target_link": "lnk_...", '
        '"grain_semantics": "...", "description": "..."}`, `AddTableBinding`) — '
        'example: `{"op_type": "AddConcept", "concept_uri": "urn:ns:Name", "concept_name": "Name", '
        '"description": "...", "key_authority_tier": "TIER_1_GLOBAL_STANDARD", '
        '"table_bindings": [{"source_id": "...", "table_id": "...", "col_name": "..."}]}`.',
    ]
    if hint:
        parts.append(f"Steward hint: {hint}")
    if answers:
        parts.append(f"Steward Hold answers: {answers}")
    return "\n\n".join(parts)
