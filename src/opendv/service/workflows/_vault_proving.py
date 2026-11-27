"""Proving-sample cache persistence, HMAC salt management, and auto-invalidation helpers."""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from opendv.models.errors import OpenDVError
from opendv.models.modeling_preferences import ProvingSampleCacheFile
from opendv.models.sources import SourceKind, SourceProfile
from opendv.service.bundles.zip_guard import (
    reject_symlinks_on_path_and_ancestors,
    validate_containment,
)
from opendv.service.os_platform import atomic_write_text, set_owner_only_permissions
from opendv.service.secrets import KeyringSecretStore
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._source_docs import attach_driver_remediation
from opendv.sources import (
    build_proving_cache_file,
    compute_salt_digest,
    is_cached_table_valid,
    profile_source_with_samples,
    tokenize_row_pii,
    validate_source_id_slug,
)


def proving_samples_dir(state_manager: ProjectStateManager, project_id: str) -> Path:
    """Return .opendv/projects/<project_id>/proving_samples/ directory path."""
    return state_manager.project_dir(project_id) / "proving_samples"


def proving_sample_path(
    state_manager: ProjectStateManager,
    project_id: str,
    source_id: str,
) -> Path:
    """Return validated owner-contained JSON cache path for a source's proving samples."""
    safe_id = validate_source_id_slug(source_id)
    base_dir = proving_samples_dir(state_manager, project_id)
    target = base_dir / f"{safe_id}.json"
    if base_dir.exists():
        validate_containment(base_dir, target)
    return target


def get_or_create_proving_salt(
    secret_store: KeyringSecretStore,
    project_id: str,
) -> tuple[str, str, str]:
    """Return (salt_key_id, salt_hex, salt_digest) for project_id, creating 32-byte hex salt."""
    salt_key = f"PROVING_SALT_{project_id}"
    salt_hex = secret_store.get_secret(salt_key)
    if not salt_hex:
        salt_hex = secrets.token_hex(32)
        secret_store.set_secret(salt_key, salt_hex)
    return salt_key, salt_hex, compute_salt_digest(salt_hex)


def save_proving_sample_cache(
    state_manager: ProjectStateManager,
    *,
    project_id: str,
    source_id: str,
    profile: SourceProfile,
    rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    sample_flags: Mapping[str, bool],
    proving_sample_rows: int,
    salt_key_id: str,
    salt_hex: str,
    salt_digest: str,
) -> Path | None:
    """Tokenize rows via HMAC salt and persist proving_samples/<source_id>.json safely."""
    if not rows_by_table:
        return None

    tokenized_by_table: dict[str, list[dict[str, Any]]] = {}
    for tbl in profile.tables:
        raw_rows = rows_by_table.get(tbl.table_id)
        if raw_rows is None:
            raw_rows = rows_by_table.get(tbl.table_name)
        if raw_rows is None:
            continue
        pii_cols = {c.col_name for c in tbl.columns if c.pii_tags}
        tokenized_by_table[tbl.table_id] = [
            tokenize_row_pii(row, pii_columns=pii_cols, salt=salt_hex)
            for row in raw_rows
        ]

    cache_file = build_proving_cache_file(
        source_id=source_id,
        project_id=project_id,
        tables=profile.tables,
        rows_by_table=tokenized_by_table,
        sample_flags=sample_flags,
        salt_key_id=salt_key_id,
        salt_digest=salt_digest,
        cached_sample_rows_limit=proving_sample_rows,
    )
    cache_path = proving_sample_path(state_manager, project_id, source_id)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    set_owner_only_permissions(cache_path.parent)
    atomic_write_text(cache_path, cache_file.model_dump_json(), owner_only=True)
    set_owner_only_permissions(cache_path)
    return cache_path


def delete_proving_sample_cache(
    state_manager: ProjectStateManager,
    project_id: str,
    source_id: str,
) -> None:
    """Remove cached proving samples for source_id if present."""
    try:
        cache_path = proving_sample_path(state_manager, project_id, source_id)
    except OpenDVError:
        return
    if cache_path.is_file():
        cache_path.unlink(missing_ok=True)


def load_cached_proving_samples(
    state_manager: ProjectStateManager,
    secret_store: KeyringSecretStore | None,
    project_id: str,
    profiles: Sequence[SourceProfile],
    *,
    proving_sample_rows: int,
    real_row_proving: bool,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, bool]]:
    """Load cached proving samples, auto-invalidating mismatched fingerprints or salt digests."""
    if not real_row_proving or not profiles:
        return {}, {}
    pdir = proving_samples_dir(state_manager, project_id)
    if not pdir.is_dir():
        return {}, {}
    effective_secrets = (
        secret_store
        if secret_store is not None
        else KeyringSecretStore(state_manager.workspace_root)
    )
    salt_hex = effective_secrets.get_secret(f"PROVING_SALT_{project_id}")
    if not salt_hex:
        return {}, {}
    expected_digest = compute_salt_digest(salt_hex)

    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    sample_flags: dict[str, bool] = {}
    for prof in profiles:
        try:
            cache_path = proving_sample_path(state_manager, project_id, prof.source_id)
        except OpenDVError:
            continue
        if not cache_path.is_file():
            continue
        try:
            reject_symlinks_on_path_and_ancestors(cache_path, stop_roots=(pdir,))
            cache_obj = ProvingSampleCacheFile.model_validate_json(
                cache_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError, OpenDVError):
            continue
        if (
            cache_obj.project_id != project_id
            or cache_obj.source_id != prof.source_id
            or cache_obj.salt_digest != expected_digest
        ):
            continue
        for tbl in prof.tables:
            cached_tbl = cache_obj.tables.get(tbl.table_id)
            if cached_tbl is None:
                continue
            if not is_cached_table_valid(
                cached_tbl,
                tbl,
                expected_salt_digest=expected_digest,
                expected_limit=proving_sample_rows,
            ):
                continue
            rows_by_table[tbl.table_id] = [dict(r) for r in cached_tbl.rows]
            sample_flags[tbl.table_id] = cached_tbl.is_complete_table

    return rows_by_table, sample_flags


@dataclass(frozen=True)
class ProvingSampleBatch:
    """In-memory proving sample batch ready to persist after lock checks."""

    project_id: str
    source_id: str
    profile: SourceProfile
    rows_by_table: dict[str, list[dict[str, Any]]]
    sample_flags: dict[str, bool]
    proving_sample_rows: int
    salt_key_id: str
    salt_hex: str
    salt_digest: str


def profile_structured_with_proving(
    secret_store: KeyringSecretStore,
    project_id: str,
    source_id: str,
    kind: SourceKind,
    uri_str: str,
    *,
    proving_sample_rows: int,
    real_row_proving: bool,
    options: Mapping[str, Any] | None = None,
    secret_refs: Sequence[str] = (),
    selected_tables: Sequence[str] | None = None,
    profile_timeout_seconds: int = 30,
    access_token: str | None = None,
) -> tuple[SourceProfile, ProvingSampleBatch]:
    """Profile structured source with project proving salt and return profile + batch."""
    salt_key, salt_hex, salt_digest = get_or_create_proving_salt(secret_store, project_id)
    try:
        profile, rows_by_table, sample_flags = profile_source_with_samples(
            source_id,
            kind,
            uri_str,
            options=options,
            secret_refs=secret_refs,
            selected_tables=selected_tables,
            profile_timeout_seconds=profile_timeout_seconds,
            proving_sample_rows=proving_sample_rows,
            real_row_proving=real_row_proving,
            pii_salt=salt_hex,
            access_token=access_token,
        )
    except OpenDVError as exc:
        raise attach_driver_remediation(exc) from exc
    return profile, ProvingSampleBatch(
        project_id=project_id,
        source_id=source_id,
        profile=profile,
        rows_by_table=rows_by_table,
        sample_flags=sample_flags,
        proving_sample_rows=proving_sample_rows,
        salt_key_id=salt_key,
        salt_hex=salt_hex,
        salt_digest=salt_digest,
    )


def sync_proving_sample_cache(
    state_manager: ProjectStateManager,
    batch: ProvingSampleBatch,
) -> None:
    """Persist proving sample cache when rows exist, or clear stale cache if empty."""
    if batch.rows_by_table:
        save_proving_sample_cache(
            state_manager,
            project_id=batch.project_id,
            source_id=batch.source_id,
            profile=batch.profile,
            rows_by_table=batch.rows_by_table,
            sample_flags=batch.sample_flags,
            proving_sample_rows=batch.proving_sample_rows,
            salt_key_id=batch.salt_key_id,
            salt_hex=batch.salt_hex,
            salt_digest=batch.salt_digest,
        )
    else:
        delete_proving_sample_cache(state_manager, batch.project_id, batch.source_id)


__all__ = [
    "ProvingSampleBatch",
    "delete_proving_sample_cache",
    "get_or_create_proving_salt",
    "load_cached_proving_samples",
    "profile_structured_with_proving",
    "proving_sample_path",
    "proving_samples_dir",
    "save_proving_sample_cache",
    "sync_proving_sample_cache",
]
