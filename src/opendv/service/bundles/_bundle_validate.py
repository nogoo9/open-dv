"""Pre-validation helpers and strict schemas for .opendv.zip bundle imports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from opendv.models.diagnostics import AntiPatternJustification
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import AuditLogRecord, ExternalMcpReceipt, OcrPageReceipt
from opendv.models.ontology import GraphAOntology
from opendv.models.project import (
    ConceptMemorySpec,
    EvolutionLockSpec,
    HorizonAssumptionSpec,
    OntologyProposalSpec,
    OntologyWaiverSpec,
    RevisionMetadata,
    StagedCandidateSpec,
)
from opendv.models.sources import DocumentMap, SourceProfile, TableIndexEntry
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.service.bundles._bundle_ports import make_revision_id

_REV_MODEL_BY_KIND: dict[str, type[BaseModel]] = {
    "ontology_version": GraphAOntology,
    "wave_ir": CertifiedVaultIR,
    "evolution_lock": EvolutionLockSpec,
    "horizon_assumption": HorizonAssumptionSpec,
    "concept_memory": ConceptMemorySpec,
    "staged_candidate": StagedCandidateSpec,
    "ontology_proposal": OntologyProposalSpec,
    "vault_waiver": AntiPatternJustification,
    "ontology_waiver": OntologyWaiverSpec,
}

_OPTIONAL_REV_FILES: tuple[tuple[str, str], ...] = (
    ("vault_waivers.json", "vault_waiver"),
    ("evolution_locks.json", "evolution_lock"),
    ("horizon_assumptions.json", "horizon_assumption"),
    ("concept_memory.json", "concept_memory"),
    ("staged_candidates.json", "staged_candidate"),
    ("ontology_proposals.json", "ontology_proposal"),
    ("ontology_waivers.json", "ontology_waiver"),
)


class SourceDigestEntry(BaseModel):
    """Strict schema for a single source entry in source_digests.json."""

    model_config = ConfigDict(strict=True, extra="ignore")

    binary_sha256: str | None = None
    approved_scan_pages: list[int] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PrevalidatedBundle:
    """Validated in-memory bundle models prior to any disk or DB mutation."""

    profiles_by_src: dict[str, SourceProfile]
    tables_by_src: dict[str, list[TableIndexEntry]]
    doc_maps_by_id: dict[str, DocumentMap]
    source_digests: dict[str, SourceDigestEntry]
    validated_revs: list[tuple[str, str, str, str | None, BaseModel, RevisionMetadata]]
    mcp_receipts: list[ExternalMcpReceipt]
    ocr_receipts: list[OcrPageReceipt]
    audit_records: list[AuditLogRecord]


def parse_json(raw: bytes | None, default: Any, label: str) -> Any:
    """Decode UTF-8 JSON bytes or return default when absent."""
    if raw is None:
        return default
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenDVError(
            f"Invalid JSON in bundle entry '{label}': {exc}",
            error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        ) from exc


def parse_json_list(raw: bytes | None, label: str) -> list[Any]:
    """Decode UTF-8 JSON bytes and require a top-level JSON array."""
    parsed = parse_json(raw, [], label)
    if not isinstance(parsed, list):
        raise OpenDVError(
            f"Invalid '{label}' list in bundle: expected JSON array.",
            error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        )
    return parsed


def parse_jsonl(raw: bytes | None, label: str) -> list[dict[str, Any]]:
    """Decode UTF-8 JSONL rows from bundle bytes."""
    if not raw:
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OpenDVError(
            f"Invalid UTF-8 in bundle entry '{label}': {exc}",
            error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        ) from exc
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise OpenDVError(
                f"Invalid JSONL line in '{label}': {exc}",
                error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
            ) from exc
        if not isinstance(obj, dict):
            raise OpenDVError(
                f"Invalid JSONL line in '{label}': expected JSON object.",
                error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
            )
        rows.append(obj)
    return rows


def validate_source_digests(raw: bytes | None) -> dict[str, SourceDigestEntry]:
    """Validate source_digests.json strictly as dict[str, SourceDigestEntry]."""
    parsed = parse_json(raw, {}, "source_digests.json")
    if not isinstance(parsed, dict):
        raise OpenDVError(
            "Invalid 'source_digests.json' in bundle: expected JSON object.",
            error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        )
    out: dict[str, SourceDigestEntry] = {}
    for src_id, meta in parsed.items():
        if not isinstance(src_id, str) or not isinstance(meta, dict):
            raise OpenDVError(
                "Invalid entry in 'source_digests.json': expected object per source_id.",
                error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
            )
        try:
            out[src_id] = SourceDigestEntry.model_validate(meta)
        except ValidationError as exc:
            raise OpenDVError(
                f"Invalid source_digests entry for '{src_id}': {exc}",
                error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
            ) from exc
    return out


def prevalidate_bundle_entries(
    entries: dict[str, bytes],
    orig_pid: str,
    resolved_pid: str,
) -> PrevalidatedBundle:
    """Validate all bundle metadata, revisions, and receipts before disk or DB writes."""
    try:
        profiles_list = [
            SourceProfile.model_validate(p)
            for p in parse_json_list(entries.get("profile.json"), "profile.json")
        ]
        profiles_by_src = {p.source_id: p for p in profiles_list}

        t_idx_list = [
            TableIndexEntry.model_validate(t)
            for t in parse_json_list(entries.get("tables_index.json"), "tables_index.json")
        ]
        tables_by_src: dict[str, list[TableIndexEntry]] = {}
        for t_entry in t_idx_list:
            tables_by_src.setdefault(t_entry.source_id, []).append(t_entry)

        doc_maps = [
            DocumentMap.model_validate(d)
            for d in parse_json_list(entries.get("document_map.json"), "document_map.json")
        ]
        doc_maps_by_id = {d.doc_id: d for d in doc_maps}
        source_digests = validate_source_digests(entries.get("source_digests.json"))

        rev_envelopes: list[tuple[str, dict[str, Any]]] = []
        for name, raw_bytes in sorted(entries.items()):
            if name.startswith("versions/") and name.endswith(".json"):
                rev_envelopes.append(("ontology_version", parse_json(raw_bytes, {}, name)))
            elif name.startswith("waves/") and name.endswith(".json"):
                rev_envelopes.append(("wave_ir", parse_json(raw_bytes, {}, name)))
        for fname, r_kind in _OPTIONAL_REV_FILES:
            if fname in entries:
                for item in parse_json_list(entries[fname], fname):
                    rev_envelopes.append((r_kind, item))

        validated_revs: list[
            tuple[str, str, str, str | None, BaseModel, RevisionMetadata]
        ] = []
        for r_kind, env in rev_envelopes:
            if not isinstance(env, dict):
                raise OpenDVError(
                    f"Invalid revision envelope for '{r_kind}': expected JSON object.",
                    error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
                )
            model_cls = _REV_MODEL_BY_KIND[r_kind]
            payload_obj = model_cls.model_validate(env.get("payload", {}))
            if isinstance(payload_obj, CertifiedVaultIR) and resolved_pid != orig_pid:
                payload_obj = payload_obj.model_copy(update={"project_id": resolved_pid})
            raw_meta = env.get("metadata") or {"actor": "system", "reason": "bundle_import"}
            if not isinstance(raw_meta, dict):
                raise OpenDVError(
                    f"Invalid revision metadata for '{r_kind}'.",
                    error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
                )
            if raw_meta.get("actor") not in ("human", "agent"):
                raw_meta = {**raw_meta, "actor": "human"}
            meta_obj = RevisionMetadata.model_validate(raw_meta)
            r_key = str(env.get("rev_key", ""))
            rev_id = str(env.get("revision_id") or "") or make_revision_id(
                r_kind, r_key, payload_obj
            )
            raw_parent = env.get("parent_rev_id")
            parent_rev_id = str(raw_parent) if raw_parent is not None else None
            validated_revs.append((rev_id, r_kind, r_key, parent_rev_id, payload_obj, meta_obj))

        mcp_receipts = [
            ExternalMcpReceipt.model_validate(r)
            for r in parse_jsonl(
                entries.get("external_mcp_receipts.jsonl"), "external_mcp_receipts.jsonl"
            )
        ]
        ocr_receipts = [
            OcrPageReceipt.model_validate(r)
            for r in parse_jsonl(entries.get("ocr_receipts.jsonl"), "ocr_receipts.jsonl")
        ]
        audit_records = [
            AuditLogRecord.model_validate(r)
            for r in parse_jsonl(entries.get("audit.jsonl"), "audit.jsonl")
        ]
    except ValidationError as exc:
        raise OpenDVError(
            f"Invalid bundle metadata or revision payload: {exc}",
            error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        ) from exc

    return PrevalidatedBundle(
        profiles_by_src=profiles_by_src,
        tables_by_src=tables_by_src,
        doc_maps_by_id=doc_maps_by_id,
        source_digests=source_digests,
        validated_revs=validated_revs,
        mcp_receipts=mcp_receipts,
        ocr_receipts=ocr_receipts,
        audit_records=audit_records,
    )


__all__ = [
    "PrevalidatedBundle",
    "SourceDigestEntry",
    "parse_json",
    "parse_json_list",
    "parse_jsonl",
    "prevalidate_bundle_entries",
    "validate_source_digests",
]
