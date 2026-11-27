"""Pure-JSON runtime FIBO Pack v2 installation, index verification, and 3-tier drift audit."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from opendv.grounding.providers.fibo.loader import (
    MAX_DISK_SIZE_BYTES,
    FiboReferenceOntologyLoader,
    get_default_fibo_loader,
)
from opendv.models.errors import ErrorCode, OpenDVError, ReferenceOntologyCorruptError
from opendv.models.events import GroundingReceipt
from opendv.models.grounding import (
    FiboDriftItem,
    FiboProjectDriftReport,
    FiboSyncResp,
)
from opendv.models.json_hasher import dumps_jcs_bytes
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ExternalMapping,
    GraphAOntology,
)
from opendv.models.project import WINDOWS_RESERVED_NAMES
from opendv.service.bundles.zip_guard import safe_open_no_follow, validate_containment
from opendv.service.os_platform import (
    atomic_write_bytes,
    atomic_write_text,
    resolve_user_state_dir,
    set_owner_only_permissions,
)
from opendv.service.state_store import ProjectStateManager

_DRIVE_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z]:")
_HEX64_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TAG_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FIBO_URI_PREFIX: Final[str] = "https://spec.edmcouncil.org/fibo/ontology/"
_CMNS_URI_PREFIX: Final[str] = "https://www.omg.org/spec/Commons/"


def _validate_local_file_path(raw_path: Path | str) -> Path:
    raw = os.fspath(raw_path)
    if not raw or not raw.strip():
        raise OpenDVError(
            "Pack file path cannot be empty.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if "\x00" in raw or any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise OpenDVError(
            "Pack file path contains forbidden control or null bytes.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if raw.startswith(("\\\\", "//")):
        raise OpenDVError(
            f"UNC pack file path is forbidden: '{raw}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    remainder = raw[2:] if _DRIVE_PREFIX_RE.match(raw) else raw
    if ":" in remainder:
        raise OpenDVError(
            f"NTFS Alternate Data Stream ':' is forbidden in pack path: '{raw}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    for seg in remainder.replace("\\", "/").split("/"):
        if not seg:
            continue
        if seg == "..":
            raise OpenDVError(
                f"Path traversal '..' is forbidden in pack path: '{raw}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        stem = seg.split(".")[0].lower()
        if seg.lower() in WINDOWS_RESERVED_NAMES or stem in WINDOWS_RESERVED_NAMES:
            raise OpenDVError(
                f"Windows reserved device name forbidden in pack path: '{raw}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
    return Path(raw)


def _read_bounded_no_follow(
    path: Path,
    *,
    max_bytes: int = MAX_DISK_SIZE_BYTES,
    stop_roots: tuple[Path, ...] = (),
) -> bytes:
    if not os.path.lexists(path):
        raise OpenDVError(
            f"FIBO pack file not found: {path}",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    try:
        fd = safe_open_no_follow(path, os.O_RDONLY, stop_roots=stop_roots)
    except FileNotFoundError as exc:
        raise OpenDVError(
            f"FIBO pack file not found: {path}",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        ) from exc
    with os.fdopen(fd, "rb") as fh:
        raw_bytes = fh.read(max_bytes + 1)
    if len(raw_bytes) > max_bytes:
        raise ReferenceOntologyCorruptError(
            f"FIBO pack '{path.name}' exceeds maximum size of {max_bytes} bytes."
        )
    return raw_bytes


def resolve_packs_dir(state_dir: Path | str | None = None) -> Path:
    """Resolve and secure <OS_USER_STATE>/opendv/packs/fibo/ with owner-only ACLs."""
    base = resolve_user_state_dir(state_dir)
    packs_dir = (base / "packs" / "fibo").resolve()
    packs_dir.mkdir(parents=True, exist_ok=True)
    set_owner_only_permissions(packs_dir)
    return packs_dir


def _read_packs_index(packs_dir: Path) -> dict[str, Any]:
    index_path = packs_dir / "packs_index.json"
    if not os.path.lexists(index_path):
        return {"active_tag": None, "packs": {}}
    raw = _read_bounded_no_follow(index_path, max_bytes=1024 * 1024, stop_roots=(packs_dir,))
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReferenceOntologyCorruptError(
            f"Corrupt packs_index.json in '{packs_dir}': {exc}"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("packs"), dict):
        raise ReferenceOntologyCorruptError(f"Malformed packs_index.json in '{packs_dir}'.")
    return data


def _sanitize_tag(raw_tag: str) -> str:
    clean = raw_tag.strip()
    if not _SAFE_TAG_RE.fullmatch(clean) or ".." in clean:
        raise OpenDVError(
            f"Invalid FIBO pack tag '{raw_tag}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return clean


def install_fibo_pack(
    pack_file: Path | str,
    *,
    tag: str | None = None,
    state_dir: Path | str | None = None,
) -> FiboSyncResp:
    """Verify Pack v2 JCS integrity and install under <OS_USER_STATE>/opendv/packs/fibo/."""
    cand_path = _validate_local_file_path(pack_file)
    raw_bytes = _read_bounded_no_follow(cand_path, max_bytes=MAX_DISK_SIZE_BYTES)

    computed_sha = hashlib.sha256(raw_bytes).hexdigest()
    sha8 = computed_sha[:8]

    # Verify optional fibo-<tag>-<sha8>.json filename digest before parsing
    fname_match = re.match(r"^fibo-.+-([0-9a-f]{8})\.json$", cand_path.name)
    if (
        fname_match is not None
        and cand_path.name != "fibo-core-pack.json"
        and fname_match.group(1) != sha8
    ):
        raise ReferenceOntologyCorruptError(
            f"Pack filename digest '{fname_match.group(1)}' does not match SHA-256 '{sha8}'."
        )

    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReferenceOntologyCorruptError(f"Invalid FIBO pack JSON: {exc}") from exc

    if dumps_jcs_bytes(parsed) != raw_bytes:
        raise ReferenceOntologyCorruptError(
            "FIBO Pack v2 bytes are not canonical RFC 8785 JCS (zero trailing newline required)."
        )

    loader = FiboReferenceOntologyLoader.from_bytes(
        raw_bytes,
        expected_sha256=computed_sha,
        verify_integrity=True,
    )
    meta = loader.get_pack_metadata()
    raw_tag = tag if (tag is not None and tag.strip()) else str(
        meta.get("upstream_tag") or loader.version
    )
    safe_tag = _sanitize_tag(raw_tag)

    packs_dir = resolve_packs_dir(state_dir)
    dest_path = (packs_dir / f"fibo-{safe_tag}-{sha8}.json").resolve()
    validate_containment(packs_dir, dest_path)
    atomic_write_bytes(dest_path, raw_bytes, owner_only=True)

    index_data = _read_packs_index(packs_dir)
    packs_map = dict(index_data.get("packs", {}))
    packs_map[safe_tag] = {
        "installed_at": datetime.now(UTC).isoformat(),
        "pack_path": str(dest_path),
        "sha256": computed_sha,
        "upstream_tag": safe_tag,
        "version": loader.version,
    }
    updated_index = {"active_tag": safe_tag, "packs": packs_map}
    atomic_write_text(
        packs_dir / "packs_index.json",
        json.dumps(updated_index, indent=2, sort_keys=True),
        owner_only=True,
    )
    return FiboSyncResp(
        installed_pack_path=str(dest_path),
        pack_sha256=computed_sha,
        upstream_tag=safe_tag,
    )


def load_installed_pack(
    *,
    tag: str | None = None,
    state_dir: Path | str | None = None,
) -> tuple[Path, FiboReferenceOntologyLoader]:
    """Load and SHA-256-verify an installed Pack v2 from <OS_USER_STATE>/opendv/packs/fibo/."""
    packs_dir = resolve_packs_dir(state_dir)
    index_data = _read_packs_index(packs_dir)
    packs_map = index_data.get("packs", {})
    selected_tag = _sanitize_tag(tag) if tag is not None else index_data.get("active_tag")
    if not isinstance(selected_tag, str) or selected_tag not in packs_map:
        raise OpenDVError(
            f"Installed FIBO pack not found for tag '{selected_tag or 'active'}'.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )

    entry = packs_map[selected_tag]
    if not isinstance(entry, dict):
        raise ReferenceOntologyCorruptError(f"Malformed packs_index entry for '{selected_tag}'.")
    expected_sha = str(entry.get("sha256", "")).strip().lower()
    if not _HEX64_RE.fullmatch(expected_sha):
        raise ReferenceOntologyCorruptError(
            f"Invalid 64-hex sha256 '{expected_sha}' in packs_index.json for '{selected_tag}'."
        )
    raw_pack_path = entry.get("pack_path")
    if not isinstance(raw_pack_path, str) or not raw_pack_path.strip():
        raise ReferenceOntologyCorruptError(
            f"Missing pack_path in packs_index.json for '{selected_tag}'."
        )

    cand_path = _validate_local_file_path(raw_pack_path)
    packs_norm = os.path.normcase(os.path.abspath(str(packs_dir)))
    cand_norm = os.path.normcase(os.path.abspath(str(cand_path)))
    packs_prefix = packs_norm if packs_norm.endswith(os.sep) else packs_norm + os.sep
    if not cand_norm.startswith(packs_prefix):
        raise OpenDVError(
            f"Path '{cand_path}' escapes containment root '{packs_dir}'",
            error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        )
    validate_containment(packs_dir, cand_path)
    raw_bytes = _read_bounded_no_follow(
        cand_path,
        max_bytes=MAX_DISK_SIZE_BYTES,
        stop_roots=(packs_dir,),
    )
    loader = FiboReferenceOntologyLoader.from_bytes(
        raw_bytes,
        expected_sha256=expected_sha,
        verify_integrity=True,
    )
    return cand_path, loader


def plan_repin_with_receipt_verification(
    *,
    concept: CanonicalBusinessConcept,
    prior_mapping: ExternalMapping,
    replacement_uri: str,
    new_pack_id: str,
    new_pack_sha256: str,
    lexical_score: Decimal,
    receipts_by_id: Mapping[str, GroundingReceipt],
    table_fingerprints: Mapping[str, str],
) -> tuple[GroundingReceipt | None, Decimal, str | None]:
    """Compute R5-B1 repin confidence & receipt strictly from verified server receipts.

    Mints a fresh ``server_verified`` ``GroundingReceipt`` carrying
    ``max(prior_receipt.confidence, lexical_score)`` ONLY when the prior
    ``grounding_receipt_id`` resolves to an existing ``server_verified`` receipt
    matching ``(old_standard_uri, table_id, schema_fingerprint)`` and
    ``concept.table_bindings``; otherwise returns ``(None, lexical_score, None)``.
    """
    prior_id = prior_mapping.grounding_receipt_id
    prior_receipt = receipts_by_id.get(prior_id) if prior_id else None

    verified = False
    if (
        prior_receipt is not None
        and prior_receipt.provenance == "server_verified"
        and prior_receipt.standard_uri == prior_mapping.standard_uri
        and bool(concept.table_bindings)
    ):
        expected_fp = table_fingerprints.get(prior_receipt.table_id)
        if expected_fp is not None and prior_receipt.schema_fingerprint == expected_fp:
            for binding in concept.table_bindings:
                if binding.table_id != prior_receipt.table_id:
                    continue
                if (
                    prior_receipt.target_kind == "table_anchor"
                    or binding.col_name == prior_receipt.source_column
                ):
                    verified = True
                    break

    if not verified or prior_receipt is None:
        return None, lexical_score, None

    carried_conf = max(prior_receipt.confidence, lexical_score)
    fresh_receipt = GroundingReceipt(
        target_kind=prior_receipt.target_kind,
        table_id=prior_receipt.table_id,
        source_column=prior_receipt.source_column,
        schema_fingerprint=prior_receipt.schema_fingerprint,
        standard_uri=replacement_uri,
        reference_pack_id=new_pack_id,
        reference_pack_sha256=new_pack_sha256,
        confidence=carried_conf,
        mapping_type=prior_mapping.mapping_type,
        table_anchor_uri=(
            replacement_uri
            if prior_receipt.target_kind == "table_anchor"
            else prior_receipt.table_anchor_uri
        ),
        provenance="server_verified",
    )
    return fresh_receipt, carried_conf, fresh_receipt.receipt_id


def _is_fibo_backed_uri(uri: str | None, pack_id: str | None = None) -> bool:
    if not uri or not uri.strip():
        return False
    clean = uri.strip()
    if clean.startswith((_FIBO_URI_PREFIX, _CMNS_URI_PREFIX)):
        return True
    return pack_id == "fibo-core" and not clean.startswith("urn:opendv:")


def _resolve_replacement_chain(loader: FiboReferenceOntologyLoader, uri: str) -> str | None:
    seen: set[str] = {uri}
    cur: str | None = loader.get_replaced_by(uri)
    while cur is not None:
        if cur in seen:
            return None
        seen.add(cur)
        target_entry = loader.lookup_by_uri(cur)
        if target_entry is not None and not target_entry.get("deprecated"):
            return cur
        cur = loader.get_replaced_by(cur)
    return None


def _audit_uri_entry(
    *,
    loader: FiboReferenceOntologyLoader,
    target_kind: str,
    target_id: str,
    uri: str,
) -> FiboDriftItem | None:
    entry = loader.lookup_by_uri(uri)
    if entry is None:
        return FiboDriftItem(
            tier="removed_or_split",
            target_kind=target_kind,  # type: ignore[arg-type]
            target_id=target_id,
            old_standard_uri=uri,
            replacement_uri=None,
            detail=f"FIBO URI '{uri}' is absent from pack '{loader.pack_sha256_prefix}'.",
        )
    if entry.get("deprecated"):
        repl = _resolve_replacement_chain(loader, uri)
        if repl is not None:
            return FiboDriftItem(
                tier="deprecated_with_replacement",
                target_kind=target_kind,  # type: ignore[arg-type]
                target_id=target_id,
                old_standard_uri=uri,
                replacement_uri=repl,
                detail=f"Deprecated FIBO URI '{uri}' is replaced by '{repl}'.",
            )
        return FiboDriftItem(
            tier="removed_or_split",
            target_kind=target_kind,  # type: ignore[arg-type]
            target_id=target_id,
            old_standard_uri=uri,
            replacement_uri=None,
            detail=f"Deprecated FIBO URI '{uri}' has no active replacement in target pack.",
        )
    return None


def _load_target_pack(
    *,
    pack_file: Path | str | None,
    tag: str | None,
    state_dir: Path | str | None,
) -> FiboReferenceOntologyLoader:
    if pack_file is not None:
        cand = _validate_local_file_path(pack_file)
        raw = _read_bounded_no_follow(cand, max_bytes=MAX_DISK_SIZE_BYTES)
        return FiboReferenceOntologyLoader.from_bytes(raw, verify_integrity=True)
    if tag is not None:
        _, loader = load_installed_pack(tag=tag, state_dir=state_dir)
        return loader
    packs_dir = resolve_packs_dir(state_dir)
    index_data = _read_packs_index(packs_dir)
    if index_data.get("active_tag"):
        _, loader = load_installed_pack(tag=None, state_dir=state_dir)
        return loader
    return get_default_fibo_loader()


def check_project_drift(
    state_manager: ProjectStateManager,
    project_id: str,
    *,
    pack_file: Path | str | None = None,
    tag: str | None = None,
    state_dir: Path | str | None = None,
    apply_repin: bool = False,
) -> FiboProjectDriftReport:
    """Audit active GraphAOntology FIBO mappings across all 4 target_kinds against Pack v2."""
    loader = _load_target_pack(pack_file=pack_file, tag=tag, state_dir=state_dir)
    project = state_manager.get_project(project_id)
    if project.active_ontology_rev_id is None:
        return FiboProjectDriftReport(
            project_id=project_id,
            pack_sha256_orig=None,
            pack_sha256_new=loader.pack_sha256,
            items=[],
            repin_deltas_applied=0,
        )

    rev = state_manager.get_revision(project_id, project.active_ontology_rev_id)
    ontology = GraphAOntology.model_validate(rev["payload"])
    items: list[FiboDriftItem] = []
    pack_sha256_orig: str | None = None

    for concept in ontology.concepts:
        for mapping in concept.external_mappings:
            if not _is_fibo_backed_uri(mapping.standard_uri, mapping.reference_pack_id):
                continue
            if pack_sha256_orig is None and mapping.reference_pack_sha256:
                pack_sha256_orig = mapping.reference_pack_sha256
            if mapping.reference_pack_sha256 != loader.pack_sha256:
                items.append(
                    FiboDriftItem(
                        tier="pack_sha256_mismatch",
                        target_kind="concept",
                        target_id=concept.concept_uri,
                        old_standard_uri=mapping.standard_uri,
                        replacement_uri=None,
                        detail=(
                            f"Bound pack SHA-256 '{mapping.reference_pack_sha256}' differs from "
                            f"target pack '{loader.pack_sha256}'."
                        ),
                    )
                )
            drift = _audit_uri_entry(
                loader=loader,
                target_kind="concept",
                target_id=concept.concept_uri,
                uri=mapping.standard_uri,
            )
            if drift is not None:
                items.append(drift)

        for attr in concept.attributes:
            if _is_fibo_backed_uri(attr.standard_property_uri):
                drift = _audit_uri_entry(
                    loader=loader,
                    target_kind="attribute",
                    target_id=f"{concept.concept_uri}::{attr.name}",
                    uri=str(attr.standard_property_uri),
                )
                if drift is not None:
                    items.append(drift)

    for rel in ontology.relationships:
        if _is_fibo_backed_uri(rel.standard_property_uri):
            drift = _audit_uri_entry(
                loader=loader,
                target_kind="relationship",
                target_id=rel.relationship_uri,
                uri=str(rel.standard_property_uri),
            )
            if drift is not None:
                items.append(drift)

    for enum_obj in ontology.enumerations:
        if _is_fibo_backed_uri(enum_obj.standard_uri):
            drift = _audit_uri_entry(
                loader=loader,
                target_kind="enumeration",
                target_id=enum_obj.enum_uri,
                uri=str(enum_obj.standard_uri),
            )
            if drift is not None:
                items.append(drift)

    repin_count = 0
    if apply_repin:
        from opendv.service.workflows._ontology_human_ops import apply_fibo_repin

        repin_count = apply_fibo_repin(
            state_manager,
            project_id,
            project.active_ontology_rev_id,
            ontology,
            loader,
            items,
            plan_fn=plan_repin_with_receipt_verification,
        )

    return FiboProjectDriftReport(
        project_id=project_id,
        pack_sha256_orig=pack_sha256_orig,
        pack_sha256_new=loader.pack_sha256,
        items=items,
        repin_deltas_applied=repin_count,
    )


__all__ = [
    "check_project_drift",
    "install_fibo_pack",
    "load_installed_pack",
    "plan_repin_with_receipt_verification",
    "resolve_packs_dir",
]
