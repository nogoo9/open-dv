"""CAS Mark-and-Sweep Garbage Collection & Source Removal (docs/19 §6.5)."""

from __future__ import annotations

import json
import logging
import re
import secrets
from pathlib import Path
from typing import Any

from opendv_service.studio.atomic_commit_errors import (
    SourceNotFoundError,
    SourceReferencedByCommittedVersionError,
    SourceReferencedByPendingProposalError,
    atomic_write_bytes,
)
from opendv_service.studio.cas_storage import ContentAddressableStorage
from opendv_service.studio.locks import DEFAULT_LOCK_TIMEOUT_SECONDS, reentrant_session_lock

logger = logging.getLogger(__name__)


def _iter_manifest_entries(m_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect source/file descriptor dicts from BOTH manifest['sources'] and manifest['files']."""
    entries: list[dict[str, Any]] = []
    for key in ("sources", "files"):
        raw = m_data.get(key)
        if isinstance(raw, dict):
            for v in raw.values():
                if isinstance(v, dict):
                    entries.append(v)
        elif isinstance(raw, list):
            for v in raw:
                if isinstance(v, dict):
                    entries.append(v)
    return entries


def mark_and_sweep_cas(
    session_dir: Path | str,
    dry_run: bool = False,
    timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> set[str]:
    """Execute fail-closed mark-and-sweep garbage collection over session inputs/cas/ under L5.

    Fail-closed invariant:
    - Acquires L5 `.session.lock` (reentrant if already held).
    - If `inputs/manifest.json` is missing, unreadable, or corrupt JSON, returns `set()`
      immediately with ZERO deletions.
    - Scans BOTH `manifest["sources"]` and `manifest["files"]`, `evidence/chunks.jsonl`,
      `evidence/tables.jsonl`, `clarifications/*.json`, proposals with status in
      `("PENDING", "STAGED", "DRAFT", "STALE")`, and
      `versions/*/{ontology,delta,meta,metadata,evaluation}.json`.
    """
    sdir = Path(session_dir).resolve()
    if not sdir.exists():
        return set()

    lock_path = sdir / ".session.lock"
    with reentrant_session_lock(lock_path, session_id=sdir.name, timeout=timeout_sec):
        cas = ContentAddressableStorage(root_dir=sdir / "inputs")
        all_blobs = set(cas.list_blobs())
        if not all_blobs:
            return set()

        # 1. Fail-closed manifest.json load (BOTH 'sources' and 'files')
        manifest_path = sdir / "inputs" / "manifest.json"
        if not manifest_path.is_file():
            logger.warning(
                "manifest.json missing in session '%s'; aborting CAS GC fail-closed.", sdir.name
            )
            return set()

        try:
            m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Corrupt/unreadable manifest.json for CAS GC (fail-closed): %s", exc)
            return set()

        if not isinstance(m_data, dict):
            logger.warning("Non-dict manifest.json for CAS GC (fail-closed).")
            return set()

        reachable: set[str] = set()
        tombstoned_source_ids: set[str] = set()

        for src in _iter_manifest_entries(m_data):
            sid = src.get("source_id") or src.get("id")
            is_tomb = bool(src.get("tombstoned") or src.get("deleted"))
            if is_tomb:
                if sid:
                    tombstoned_source_ids.add(str(sid))
            else:
                for key in ("sha256", "blob_sha256", "content_sha256", "source_sha256"):
                    val = src.get(key)
                    if (
                        isinstance(val, str)
                        and len(val) == 64
                        and re.fullmatch(r"[0-9a-fA-F]{64}", val)
                    ):
                        reachable.add(val.lower())

        # 2. Active citations in evidence/chunks.jsonl & evidence/tables.jsonl
        for ev_name in ("chunks.jsonl", "tables.jsonl"):
            ev_path = sdir / "evidence" / ev_name
            if ev_path.is_file():
                try:
                    for line in ev_path.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        rec = json.loads(line)
                        if not isinstance(rec, dict):
                            continue
                        rec_meta = (
                            rec.get("metadata") if isinstance(rec.get("metadata"), dict) else {}
                        )
                        src_id = str(
                            rec.get("source_id")
                            or rec.get("document_id")
                            or rec_meta.get("source_id")
                            or ""
                        )
                        is_rec_tomb = bool(rec.get("tombstoned") or rec_meta.get("tombstoned"))
                        if src_id in tombstoned_source_ids or is_rec_tomb:
                            continue
                        _extract_sha256_recursively(rec, reachable)
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning("Error reading %s for CAS GC (fail-closed): %s", ev_name, exc)
                    return set()

        # 3. Clarifications in clarifications/*.json
        clarifications_dir = sdir / "clarifications"
        if clarifications_dir.is_dir():
            for cfile in clarifications_dir.glob("*.json"):
                try:
                    cdata = json.loads(cfile.read_text(encoding="utf-8"))
                    _extract_sha256_recursively(cdata, reachable)
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning(
                        "Error reading clarification %s for CAS GC (fail-closed): %s",
                        cfile.name,
                        exc,
                    )
                    return set()

        # 4. Active and stale proposals in proposals/*.json
        proposals_dir = sdir / "proposals"
        if proposals_dir.is_dir():
            for pfile in proposals_dir.glob("*.json"):
                try:
                    pdata = json.loads(pfile.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning(
                        "Error reading proposal %s for CAS GC (fail-closed): %s",
                        pfile.name,
                        exc,
                    )
                    return set()
                if isinstance(pdata, dict) and pdata.get("status") in (
                    "PENDING",
                    "STAGED",
                    "DRAFT",
                    "STALE",
                    "PROPOSED",
                    None,
                ):
                    _extract_sha256_recursively(pdata, reachable)

        # 5. Committed versions in versions/*/
        versions_dir = sdir / "versions"
        if versions_dir.is_dir():
            for vdir in versions_dir.iterdir():
                if vdir.is_dir():
                    for fname in (
                        "ontology.json",
                        "delta.json",
                        "meta.json",
                        "metadata.json",
                        "evaluation.json",
                    ):
                        fpath = vdir / fname
                        if fpath.is_file():
                            try:
                                vdata = json.loads(fpath.read_text(encoding="utf-8"))
                            except (OSError, json.JSONDecodeError) as exc:
                                logger.warning(
                                    "Error reading %s/%s for CAS GC (fail-closed): %s",
                                    vdir.name,
                                    fname,
                                    exc,
                                )
                                return set()
                            _extract_sha256_recursively(vdata, reachable)

        orphans = all_blobs - reachable
        if not dry_run:
            for orphan_sha in orphans:
                cas.delete(orphan_sha)

        return orphans


def _extract_sha256_recursively(obj: Any, out_set: set[str]) -> None:
    """Recursively discover 64-character hex strings in nested structures."""
    if isinstance(obj, str):
        if len(obj) == 64 and re.fullmatch(r"[0-9a-fA-F]{64}", obj):
            out_set.add(obj.lower())
    elif isinstance(obj, dict):
        for v in obj.values():
            _extract_sha256_recursively(v, out_set)
    elif isinstance(obj, list):
        for item in obj:
            _extract_sha256_recursively(item, out_set)


def delete_source(
    session_dir: Path | str,
    source_id: str,
    timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> set[str]:
    """Mark a source as tombstoned and purge its unreferenced blobs under L5 session lock.

    Raises:
        SourceNotFoundError: If `source_id` does not exist in `manifest.json` (HTTP 404 / exit 3).
        SourceReferencedByPendingProposalError: If cited by an active/stale proposal (exit 4).
        SourceReferencedByCommittedVersionError: If cited by a committed version (exit 4).
    """
    sdir = Path(session_dir).resolve()
    lock_path = sdir / ".session.lock"
    sid = sdir.name

    with reentrant_session_lock(lock_path, session_id=sid, timeout=timeout_sec):
        manifest_path = sdir / "inputs" / "manifest.json"
        if not manifest_path.is_file():
            raise SourceNotFoundError(f"Source '{source_id}' not found in session '{sid}'.")

        m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(m_data, dict):
            raise SourceNotFoundError(f"Source '{source_id}' not found in session '{sid}'.")

        # Locate active source entry across BOTH manifest["sources"] and manifest["files"]
        matched_entries: list[dict[str, Any]] = []
        for entry in _iter_manifest_entries(m_data):
            entry_id = entry.get("source_id") or entry.get("id")
            is_tomb = bool(entry.get("tombstoned") or entry.get("deleted"))
            if entry_id == source_id and not is_tomb:
                matched_entries.append(entry)

        if not matched_entries:
            raise SourceNotFoundError(f"Source '{source_id}' not found in session '{sid}'.")

        # Collect both source_id AND all associated SHA-256 digests to guard against deletion
        match_tokens: set[str] = {source_id}
        source_hashes: set[str] = set()
        for entry in matched_entries:
            for key in ("sha256", "blob_sha256", "content_sha256", "source_sha256"):
                val = entry.get(key)
                if isinstance(val, str) and len(val) == 64:
                    low = val.lower()
                    match_tokens.add(low)
                    source_hashes.add(low)

        # 1. Check active and stale proposals against BOTH source_id and source_sha256
        proposals_dir = sdir / "proposals"
        if proposals_dir.is_dir():
            for pfile in proposals_dir.glob("*.json"):
                try:
                    pdata = json.loads(pfile.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(pdata, dict) and pdata.get("status") in (
                    None,
                    "PENDING",
                    "STAGED",
                    "DRAFT",
                    "STALE",
                    "PROPOSED",
                ):
                    found_sources: set[str] = set()
                    for tok in match_tokens:
                        _extract_strings_matching(pdata, tok, found_sources)
                    if found_sources:
                        raise SourceReferencedByPendingProposalError(
                            f"Cannot delete source '{source_id}': cited by proposal "
                            f"'{pfile.stem}' ({pdata.get('status')})"
                        )

        # 2. Check committed versions against BOTH source_id and source_sha256
        versions_dir = sdir / "versions"
        if versions_dir.is_dir():
            for vdir in versions_dir.iterdir():
                if vdir.is_dir():
                    for fname in ("ontology.json", "delta.json", "evaluation.json"):
                        fpath = vdir / fname
                        if fpath.is_file():
                            try:
                                vdata = json.loads(fpath.read_text(encoding="utf-8"))
                            except (OSError, json.JSONDecodeError):
                                continue
                            found_sources = set()
                            for tok in match_tokens:
                                _extract_strings_matching(vdata, tok, found_sources)
                            if found_sources:
                                raise SourceReferencedByCommittedVersionError(
                                    f"Cannot delete source '{source_id}': cited in committed "
                                    f"version '{vdir.name}'"
                                )

        # 3. Atomically mark tombstoned in manifest.json (abort before GC if write fails!)
        for entry in matched_entries:
            entry["tombstoned"] = True

        remaining_active_hashes: set[str] = set()
        for entry in _iter_manifest_entries(m_data):
            if bool(entry.get("tombstoned") or entry.get("deleted")):
                continue
            for key in ("sha256", "blob_sha256", "content_sha256", "source_sha256"):
                val = entry.get(key)
                if isinstance(val, str) and len(val) == 64:
                    remaining_active_hashes.add(val.lower())

        atomic_write_bytes(
            manifest_path,
            json.dumps(m_data, indent=2).encode("utf-8"),
            tmp_name=f".manifest.{secrets.token_hex(4)}.tmp",
        )

        # 4. Atomically tombstone chunks & tables in evidence/{chunks,tables}.jsonl
        for ev_name in ("chunks.jsonl", "tables.jsonl"):
            ev_path = sdir / "evidence" / ev_name
            if ev_path.is_file():
                lines = ev_path.read_text(encoding="utf-8").splitlines()
                updated_lines: list[str] = []
                for line in lines:
                    line = line.strip()
                    if line:
                        c = json.loads(line)
                        c_meta = c.get("metadata") if isinstance(c.get("metadata"), dict) else {}
                        c_sid = str(
                            c.get("source_id")
                            or c.get("document_id")
                            or c_meta.get("source_id")
                            or ""
                        )
                        c_sha = str(
                            c.get("source_sha256")
                            or c.get("blob_sha256")
                            or c.get("sha256")
                            or c_meta.get("source_sha256")
                            or ""
                        ).lower()
                        if c_sid == source_id or (
                            not c_sid
                            and c_sha
                            and c_sha in source_hashes
                            and c_sha not in remaining_active_hashes
                        ):
                            c["tombstoned"] = True
                            if isinstance(c.get("metadata"), dict):
                                c["metadata"]["tombstoned"] = True
                        updated_lines.append(json.dumps(c))
                atomic_write_bytes(
                    ev_path,
                    ("\n".join(updated_lines) + ("\n" if updated_lines else "")).encode("utf-8"),
                    tmp_name=f".{ev_name}.{secrets.token_hex(4)}.tmp",
                    truncate=True,
                )

        # 5. Run CAS mark-and-sweep GC
        return mark_and_sweep_cas(sdir, dry_run=False, timeout_sec=timeout_sec)


def _extract_strings_matching(obj: Any, target: str, out_set: set[str]) -> None:
    """Find any occurrence of target string in nested structures."""
    if isinstance(obj, str):
        val = obj.lower() if len(target) == 64 else obj
        if val == target or target in val:
            out_set.add(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _extract_strings_matching(v, target, out_set)
    elif isinstance(obj, list):
        for item in obj:
            _extract_strings_matching(item, target, out_set)
