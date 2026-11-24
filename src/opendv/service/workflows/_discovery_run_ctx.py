"""Step 4 source/document context assembly, prompt formatting, and raw delta normalization."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from opendv.models.ontology import GraphAOntology
from opendv.models.project import ConceptMemorySpec
from opendv.models.sources import DocumentMap, SourceProfile
from opendv.models.wire_dtos_discovery import ConceptSummary
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._project_seeds import discover_workspace_seeds
from opendv.service.workflows._source_docs import resolve_readable_source_path
from opendv.sources import DocumentMapBuilder
from opendv.sources.fibo.loader import CANONICAL_FIBO_CORE_SHA256

_LOGICAL_TYPE_ALIASES: dict[str, Any] = {
    "STRING": "UTF8_STRING",
    "TEXT": "UTF8_STRING",
    "VARCHAR": "UTF8_STRING",
    "CHAR": "UTF8_STRING",
    "STR": "UTF8_STRING",
    "INT": "INT64",
    "INTEGER": "INT64",
    "BIGINT": "INT64",
    "SMALLINT": "INT32",
    "BOOL": "BOOLEAN",
    "DATE": "DATE32",
    "TIMESTAMP": "TIMESTAMP_UTC_US",
    "DATETIME": "TIMESTAMP_UTC_US",
    "REAL": {"kind": "DECIMAL", "precision": 18, "scale": 4},
    "DOUBLE": {"kind": "DECIMAL", "precision": 18, "scale": 4},
    "FLOAT": {"kind": "DECIMAL", "precision": 18, "scale": 4},
    "DECIMAL": {"kind": "DECIMAL", "precision": 18, "scale": 4},
    "NUMERIC": {"kind": "DECIMAL", "precision": 18, "scale": 4},
    "JSON": "JSON_DOCUMENT",
}


def normalize_logical_type(raw_val: Any) -> Any:
    """Map common SQL/LLM type shorthands onto PortableLogicalType."""
    if isinstance(raw_val, str):
        up = raw_val.strip().upper()
        if up in _LOGICAL_TYPE_ALIASES:
            return _LOGICAL_TYPE_ALIASES[up]
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
                m.setdefault("reference_pack_sha256", CANONICAL_FIBO_CORE_SHA256)
                fixed_maps.append(m)
        op["external_mappings"] = fixed_maps
    if isinstance(op.get("mapping"), dict):
        m = dict(op["mapping"])
        m.setdefault("reference_pack_id", "fibo-core")
        m.setdefault("reference_pack_sha256", CANONICAL_FIBO_CORE_SHA256)
        op["mapping"] = m
    if isinstance(op.get("attributes"), list):
        allowed_attr_keys = {
            "name",
            "logical_type",
            "sql_type_hint",
            "nullable",
            "is_business_key",
            "is_pii",
            "pii_category",
            "enum_uri",
        }
        fixed_attrs = []
        for item in op["attributes"]:
            if isinstance(item, dict):
                a = {k: v for k, v in item.items() if k in allowed_attr_keys}
                if "name" not in a and "attribute_name" in item:
                    a["name"] = item["attribute_name"]
                a["logical_type"] = normalize_logical_type(
                    a.get("logical_type", "UTF8_STRING")
                )
                a.setdefault("sql_type_hint", "VARCHAR")
                a.setdefault("nullable", True)
                a.setdefault("is_business_key", False)
                a.setdefault("is_pii", False)
                fixed_attrs.append(a)
        op["attributes"] = fixed_attrs
    if op.get("op_type") == "AddAttribute" and "attribute" not in op:
        attr_name = op.pop("attribute_name", None) or op.pop("name", None) or "attr"
        sql_hint = str(op.pop("data_type", None) or op.pop("sql_type_hint", None) or "VARCHAR")
        is_bk = bool(op.pop("is_primary_key", op.pop("is_business_key", False)))
        nullable = bool(op.pop("nullable", not is_bk))
        is_pii = bool(op.pop("is_pii", False))
        logical = normalize_logical_type(op.pop("logical_type", None) or sql_hint)
        op.pop("description", None)
        op.pop("table_bindings", None)
        op["attribute"] = {
            "name": str(attr_name),
            "logical_type": logical,
            "sql_type_hint": sql_hint,
            "nullable": nullable,
            "is_business_key": is_bk,
            "is_pii": is_pii,
        }
    elif op.get("op_type") == "AddAttribute" and isinstance(op.get("attribute"), dict):
        attr_dict = dict(op["attribute"])
        if "logical_type" in attr_dict:
            attr_dict["logical_type"] = normalize_logical_type(attr_dict["logical_type"])
        op["attribute"] = attr_dict
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
        'include `"op_type"` (`AddConcept` with inline `attributes`/`table_bindings`, '
        '`AddAttribute` with nested `"attribute": {"name", "logical_type", "sql_type_hint", '
        '"nullable", "is_business_key", "is_pii"}`, `AddRelationship`, `AddTableBinding`) — '
        'example: `{"op_type": "AddConcept", "concept_uri": "urn:ns:Name", "concept_name": "Name", '
        '"description": "...", "key_authority_tier": "TIER_1_GLOBAL_STANDARD", '
        '"table_bindings": [{"source_id": "...", "table_id": "...", "col_name": "..."}]}`.',
    ]
    if hint:
        parts.append(f"Steward hint: {hint}")
    if answers:
        parts.append(f"Steward Hold answers: {answers}")
    return "\n\n".join(parts)
