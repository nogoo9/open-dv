"""Source privacy, manifest deduplication, taint propagation, and pseudonymization helpers."""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from opendv_agent_harness.privacy import Pseudonymizer
from opendv_contracts.dtos.ingestion import (
    EvidenceChunk,
    TableStructure,
    TaintClassification,
)
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_service.studio.workspace_security import WorkspaceSecurityManager

_TAINT_RANK: Final[dict[TaintClassification, int]] = {
    TaintClassification.PUBLIC: 0,
    TaintClassification.INTERNAL: 1,
    TaintClassification.CONFIDENTIAL: 2,
    TaintClassification.RESTRICTED: 3,
}
_PII_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)


def _normalize_taint_value(
    raw_taint: TaintClassification | str | None,
    *,
    default: TaintClassification | None = None,
) -> TaintClassification | None:
    """Normalize Studio manifest taint labels ('CLEAN' or 'TAINTED') to TaintClassification."""
    if isinstance(raw_taint, TaintClassification):
        return raw_taint
    if isinstance(raw_taint, str):
        cleaned = raw_taint.strip()
        if not cleaned:
            return default
        upper = cleaned.upper()
        if upper == "CLEAN":
            return TaintClassification.PUBLIC
        if upper in ("TAINTED", "DO_NOT_SEND"):
            return TaintClassification.CONFIDENTIAL
        with contextlib.suppress(ValueError):
            return TaintClassification(cleaned.lower())
    return default


def _max_taint(*labels: TaintClassification | str | None) -> TaintClassification | None:
    """Return the monotonic maximum TaintClassification across all provided labels."""
    best: TaintClassification | None = None
    for lbl in labels:
        norm = _normalize_taint_value(lbl)
        if norm is None:
            continue
        if best is None or _TAINT_RANK[norm] > _TAINT_RANK[best]:
            best = norm
    return best


def _iter_manifest_entries_deduped(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return deduplicated entries across `manifest['files']` and `manifest['sources']`."""
    entries: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, bool]] = set()
    for bucket_name in ("files", "sources"):
        bucket = manifest.get(bucket_name)
        items: list[Any] = []
        if isinstance(bucket, dict):
            for k, v in bucket.items():
                if isinstance(v, dict):
                    items.append({**v, "source_id": v.get("source_id") or v.get("id") or k})
        elif isinstance(bucket, list):
            items = bucket
        for item in items:
            if isinstance(item, dict):
                sid = str(item.get("source_id") or item.get("id") or "")
                sha = str(
                    item.get("sha256")
                    or item.get("blob_sha256")
                    or item.get("content_sha256")
                    or item.get("source_sha256")
                    or ""
                ).lower()
                is_tomb = bool(item.get("tombstoned") or item.get("deleted"))
                key = (sid, sha, is_tomb)
                if key not in seen_keys:
                    seen_keys.add(key)
                    entries.append(item)
    return entries


def _read_session_json_dict_fail_closed(file_path: Path, label: str) -> dict[str, Any] | None:
    """Read a session JSON dictionary with Win32 atomic-replace retry; fail closed on corruption."""
    try:
        st = os.lstat(file_path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OpenDVError(
            f"Unable to stat {label} '{file_path.name}'; refusing to fail open on privacy state: "
            f"{exc}",
            error_code=ErrorCode.CORRUPT_SESSION_RECORD,
        ) from exc

    is_reparse = bool(getattr(st, "st_file_attributes", 0) & 0x0400)
    if stat.S_ISLNK(st.st_mode) or is_reparse or file_path.is_symlink():
        raise OpenDVError(
            f"Session {label} '{file_path.name}' must not be a symlink or reparse point.",
            error_code=ErrorCode.INVALID_WORKSPACE_PATH,
        )
    if not stat.S_ISREG(st.st_mode):
        return None

    last_err: Exception | None = None
    for attempt in range(6):
        try:
            raw_text = file_path.read_text(encoding="utf-8")
            parsed = json.loads(raw_text)
            if not isinstance(parsed, dict):
                raise OpenDVError(
                    f"Session {label} '{file_path.name}' must contain a JSON object; "
                    "refusing to fail open on privacy/tombstone state.",
                    error_code=ErrorCode.CORRUPT_SESSION_RECORD,
                )
            return parsed
        except FileNotFoundError:
            return None
        except PermissionError as exc:
            last_err = exc
            if attempt < 5:
                time.sleep(0.01 * (2**attempt))
                continue
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_err = exc
            if isinstance(exc, OSError) and attempt < 5:
                time.sleep(0.01 * (2**attempt))
                continue
            break

    raise OpenDVError(
        f"Failed to read or parse session {label} '{file_path.name}'; "
        f"refusing to fail open on privacy/tombstone state: {last_err}",
        error_code=ErrorCode.CORRUPT_SESSION_RECORD,
    ) from last_err


def _merge_taint_map(
    store: dict[str, TaintClassification],
    key: str,
    val: TaintClassification | None,
) -> None:
    if not key or val is None:
        return
    merged = _max_taint(store.get(key), val)
    if merged is not None:
        store[key] = merged


def _load_source_privacy_and_lifecycle_state(session_dir: Path) -> dict[str, Any]:
    """Read `inputs/manifest.json` and `taint_index.json` for sticky privacy and tombstones."""
    active_ids: set[str] = set()
    active_hashes: set[str] = set()
    active_sha_by_id: dict[str, str] = {}
    tombstoned_ids: set[str] = set()
    tombstoned_hashes: set[str] = set()
    do_not_send_ids: set[str] = set()
    do_not_send_shas: set[str] = set()
    taint_by_id: dict[str, TaintClassification] = {}
    taint_by_sha: dict[str, TaintClassification] = {}

    manifest_file = session_dir / "inputs" / "manifest.json"
    m_data = _read_session_json_dict_fail_closed(manifest_file, "inputs manifest")
    if m_data is not None:
        for entry in _iter_manifest_entries_deduped(m_data):
            sid = str(entry.get("source_id") or entry.get("id") or "")
            entry_shas = [
                str(entry.get(k)).lower()
                for k in ("sha256", "blob_sha256", "content_sha256", "source_sha256")
                if isinstance(entry.get(k), str) and len(str(entry.get(k))) == 64
            ]
            if entry.get("tombstoned") or entry.get("deleted"):
                if sid:
                    tombstoned_ids.add(sid)
                tombstoned_hashes.update(entry_shas)
            else:
                if sid:
                    active_ids.add(sid)
                    if entry_shas:
                        active_sha_by_id[sid] = entry_shas[0]
                active_hashes.update(entry_shas)

            raw_t = entry.get("taint") or entry.get("taint_classification")
            norm_t = _normalize_taint_value(raw_t)
            dns = bool(entry.get("do_not_send_to_agent") is True)
            if str(raw_t or "").strip().upper() == "DO_NOT_SEND":
                dns = True
            if dns:
                norm_t = _max_taint(norm_t, TaintClassification.CONFIDENTIAL)
                if sid:
                    do_not_send_ids.add(sid)
                do_not_send_shas.update(entry_shas)
            if sid:
                _merge_taint_map(taint_by_id, sid, norm_t)
            for h in entry_shas:
                _merge_taint_map(taint_by_sha, h, norm_t)

    tombstoned_ids -= active_ids

    taint_idx_file = session_dir / "taint_index.json"
    t_data = _read_session_json_dict_fail_closed(taint_idx_file, "taint index")
    if t_data is not None:
        for list_key in (
            "tainted_source_ids",
            "tainted_sources",
            "confidential_sources",
            "do_not_send_to_agent",
            "do_not_send_source_ids",
            "do_not_send_sources",
        ):
            coll = t_data.get(list_key)
            if isinstance(coll, list):
                for item in coll:
                    if isinstance(item, str) and item.strip():
                        clean_s = item.strip()
                        if len(clean_s) == 64 and all(
                            ch in "0123456789abcdefABCDEF" for ch in clean_s
                        ):
                            do_not_send_shas.add(clean_s.lower())
                            _merge_taint_map(
                                taint_by_sha, clean_s.lower(), TaintClassification.CONFIDENTIAL
                            )
                        else:
                            do_not_send_ids.add(clean_s)
                            _merge_taint_map(
                                taint_by_id, clean_s, TaintClassification.CONFIDENTIAL
                            )
                    elif isinstance(item, dict):
                        i_sid = str(item.get("source_id") or item.get("id") or "").strip()
                        i_sha = (
                            str(item.get("sha256") or item.get("source_sha256") or "")
                            .strip()
                            .lower()
                        )
                        i_dns = item.get("do_not_send_to_agent", True) is not False
                        i_taint = _normalize_taint_value(
                            item.get("taint") or item.get("taint_classification"),
                            default=TaintClassification.CONFIDENTIAL,
                        )
                        if i_dns:
                            i_taint = _max_taint(i_taint, TaintClassification.CONFIDENTIAL)
                            if i_sid:
                                do_not_send_ids.add(i_sid)
                            if len(i_sha) == 64:
                                do_not_send_shas.add(i_sha)
                        if i_sid:
                            _merge_taint_map(taint_by_id, i_sid, i_taint)
                        if len(i_sha) == 64:
                            _merge_taint_map(taint_by_sha, i_sha, i_taint)

        sha_list = t_data.get("tainted_sha256s")
        if isinstance(sha_list, list):
            for s_val in sha_list:
                if isinstance(s_val, str) and len(s_val.strip()) == 64:
                    clean_h = s_val.strip().lower()
                    do_not_send_shas.add(clean_h)
                    _merge_taint_map(taint_by_sha, clean_h, TaintClassification.CONFIDENTIAL)

        sources_map = t_data.get("sources")
        if isinstance(sources_map, dict):
            for k, v in sources_map.items():
                if isinstance(v, dict):
                    v_dns = bool(v.get("do_not_send_to_agent") is True)
                    v_taint = _normalize_taint_value(
                        v.get("taint") or v.get("taint_classification")
                    )
                    if v_dns:
                        v_taint = _max_taint(v_taint, TaintClassification.CONFIDENTIAL)
                        do_not_send_ids.add(str(k))
                    _merge_taint_map(taint_by_id, str(k), v_taint)

    return {
        "active_ids": active_ids,
        "active_hashes": active_hashes,
        "active_sha_by_id": active_sha_by_id,
        "tombstoned_ids": tombstoned_ids,
        "tombstoned_hashes": tombstoned_hashes,
        "do_not_send_ids": do_not_send_ids,
        "do_not_send_shas": do_not_send_shas,
        "taint_by_id": taint_by_id,
        "taint_by_sha": taint_by_sha,
    }


def _prepare_evidence_for_agent(
    chunks: Sequence[EvidenceChunk],
    tables: Sequence[TableStructure],
    security_manager: WorkspaceSecurityManager,
    sensitive_terms: Sequence[str] | None = None,
) -> tuple[list[EvidenceChunk], list[TableStructure]]:
    """Filter `do_not_send_to_agent` items and HMAC-pseudonymize non-public evidence for agents."""
    pseudonymizer = Pseudonymizer(security_manager.get_pseudo_key())
    caller_terms = [str(t).strip() for t in (sensitive_terms or ()) if str(t).strip()]
    safe_chunks: list[EvidenceChunk] = []
    for chunk in chunks:
        c_meta = dict(chunk.metadata or {})
        if c_meta.get("do_not_send_to_agent") is True:
            continue
        eff_c_taint = _max_taint(chunk.taint, c_meta.get("taint")) or TaintClassification.INTERNAL
        if eff_c_taint != TaintClassification.PUBLIC:
            eff_c_terms: list[str] = list(caller_terms)
            for email_hit in _PII_EMAIL_RE.findall(chunk.text or ""):
                if email_hit not in eff_c_terms:
                    eff_c_terms.append(email_hit)
            p_chunk = pseudonymizer.pseudonymize_chunk(chunk, sensitive_terms=eff_c_terms)
            p_meta = dict(p_chunk.metadata or {})
            p_meta["pseudonymized"] = True
            p_chunk = p_chunk.model_copy(update={"metadata": p_meta})
            safe_chunks.append(p_chunk)
        else:
            safe_chunks.append(chunk)

    safe_tables: list[TableStructure] = []
    for tbl in tables:
        t_meta = dict(tbl.metadata or {})
        if t_meta.get("do_not_send_to_agent") is True:
            continue
        eff_taint = _max_taint(tbl.taint, t_meta.get("taint")) or TaintClassification.INTERNAL
        if eff_taint != TaintClassification.PUBLIC:
            eff_terms: list[str] = list(caller_terms)
            tbl_blob = "\n".join(
                [
                    tbl.table_id or "",
                    *tbl.headers,
                    *(str(c) for r in tbl.rows for c in r),
                    tbl.caption or "",
                    tbl.raw_html_or_md or "",
                ]
            )
            for email_hit in _PII_EMAIL_RE.findall(tbl_blob):
                if email_hit not in eff_terms:
                    eff_terms.append(email_hit)
            safe_tables.append(pseudonymizer.pseudonymize_table(tbl, sensitive_terms=eff_terms))
        else:
            safe_tables.append(tbl)

    return safe_chunks, safe_tables
