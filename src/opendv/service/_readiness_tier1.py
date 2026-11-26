"""Tier-1 offline harness prerequisite evaluator and active-run lease scanner."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from opendv.agents import default_model_options, resolve_cli_binary
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.project import HarnessProvider, SecretBackend
from opendv.models.wire_dtos_readiness import ProviderReadinessRecord, ReadinessResponse
from opendv.service._remediation import build_remediation
from opendv.service._secret_manifest import SecretSource
from opendv.service._state_ddl import open_wal_connection
from opendv.service._state_leases import is_lease_row_live
from opendv.service._state_records import utc_now
from opendv.service.secrets import KeyringSecretStore

ALL_PROVIDERS: tuple[HarnessProvider, ...] = ("agy", "claude-cli", "claude", "gemini")
CredentialSource = Literal["cli_login", "store", "os_env", "none"]
ReadinessStatus = Literal["MISSING_PREREQ", "CONFIGURED_UNVERIFIED", "VERIFIED", "AUTH_FAILED"]


@dataclass(frozen=True, slots=True)
class Tier1Eval:
    """Single-pass Tier-1 prerequisite snapshot for one HarnessProvider."""

    provider: HarnessProvider
    credential_source: CredentialSource
    binary_path: str | None
    sdk_installed: bool | None
    missing_prereqs: list[str]
    missing_driver: bool
    api_key: str | None


def source_to_credential_source(source: SecretSource) -> CredentialSource:
    """Map KeyringSecretStore provenance into ProviderReadinessRecord.credential_source."""
    if source in ("keyring", "user_state_file"):
        return "store"
    if source == "env":
        return "os_env"
    return "none"


def scan_active_provider_runs(db_path: Path) -> dict[HarnessProvider, str]:
    """Return {provider: most_recent_holder_run_id} for live active_job_lease rows."""
    if not db_path.is_file():
        return {}
    conn = open_wal_connection(db_path)
    try:
        try:
            rows = conn.execute(
                """
                SELECT l.*, p.settings_snapshot_json
                FROM leases l
                JOIN projects p ON p.project_id = l.project_id
                WHERE l.lease_key LIKE 'active_job_lease:%'
                ORDER BY l.heartbeat_at DESC, l.lease_key ASC
                """
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
        now = utc_now()
        active: dict[HarnessProvider, str] = {}
        for row in rows:
            if not is_lease_row_live(row, now):
                continue
            try:
                snap = json.loads(str(row["settings_snapshot_json"]))
            except (ValueError, KeyError, TypeError):
                continue
            if not isinstance(snap, dict):
                continue
            prov = snap.get("harness_provider")
            if prov in ALL_PROVIDERS:
                typed_prov = cast(HarnessProvider, prov)
                if typed_prov not in active:
                    active[typed_prov] = str(row["holder_run_id"])
        return active
    finally:
        conn.close()


def evaluate_tier1(
    secret_store: KeyringSecretStore,
    *,
    which_fn: Callable[[str], str | None] | None,
    find_spec_fn: Callable[[str], object | None] | None,
) -> dict[HarnessProvider, Tier1Eval]:
    """Run a single-pass <50ms Tier-1 evaluation across all 4 harness providers."""
    parent_env = secret_store.effective_env()
    precedence = secret_store.resolve_precedence()
    cached_backend: SecretBackend | None = None

    def _get_backend() -> SecretBackend:
        nonlocal cached_backend
        if cached_backend is None:
            cached_backend = secret_store.effective_backend()
        return cached_backend

    ant_env = (parent_env.get("ANTHROPIC_API_KEY") or "").strip()
    if precedence == "env_first" and ant_env:
        anthropic_val: str | None = ant_env
        anthropic_src: SecretSource = "env"
    else:
        anthropic_val, anthropic_src = secret_store.get_secret_with_source(
            "ANTHROPIC_API_KEY",
            backend_kind=_get_backend(),
            precedence=precedence,
        )

    gem_env = (
        (parent_env.get("GEMINI_API_KEY") or "").strip()
        or (parent_env.get("GOOGLE_API_KEY") or "").strip()
    )
    if precedence == "env_first" and gem_env:
        gemini_val, gemini_src = secret_store.resolve_gemini_api_key_with_source(
            precedence=precedence,
        )
    else:
        gemini_val, gemini_src = secret_store.resolve_gemini_api_key_with_source(
            backend_kind=_get_backend(),
            precedence=precedence,
        )

    spec_fn = find_spec_fn or importlib.util.find_spec
    evals: dict[HarnessProvider, Tier1Eval] = {}
    cli_providers: tuple[Literal["agy", "claude-cli"], ...] = ("agy", "claude-cli")
    for cli_prov in cli_providers:
        missing: list[str] = []
        missing_driver = False
        bin_path: str | None = None
        try:
            bin_path = resolve_cli_binary(cli_prov, env=parent_env, which_fn=which_fn)
        except OpenDVError:
            missing.append(f"CLI binary '{cli_prov}' not found on PATH")
            missing_driver = True

        if cli_prov == "agy":
            cred_src: CredentialSource = "cli_login" if bin_path is not None else "none"
            key_val: str | None = None
        else:
            if anthropic_val is not None:
                cred_src = source_to_credential_source(anthropic_src)
            elif bin_path is not None:
                cred_src = "cli_login"
            else:
                cred_src = "none"
            key_val = anthropic_val
        evals[cli_prov] = Tier1Eval(
            provider=cli_prov,
            credential_source=cred_src,
            binary_path=bin_path,
            sdk_installed=None,
            missing_prereqs=missing,
            missing_driver=missing_driver,
            api_key=key_val,
        )

    claude_sdk = spec_fn("anthropic") is not None
    claude_missing: list[str] = []
    if not claude_sdk:
        claude_missing.append("Optional package 'anthropic' missing (uv sync --extra sdk)")
    if anthropic_val is None:
        claude_missing.append("ANTHROPIC_API_KEY is not configured")
    evals["claude"] = Tier1Eval(
        provider="claude",
        credential_source=source_to_credential_source(anthropic_src),
        binary_path=None,
        sdk_installed=claude_sdk,
        missing_prereqs=claude_missing,
        missing_driver=not claude_sdk,
        api_key=anthropic_val,
    )

    gemini_sdk = (spec_fn("google") is not None) and (spec_fn("google.genai") is not None)
    gemini_missing: list[str] = []
    if not gemini_sdk:
        gemini_missing.append("Optional package 'google-genai' missing (uv sync --extra sdk)")
    if gemini_val is None:
        gemini_missing.append("GEMINI_API_KEY or GOOGLE_API_KEY is not configured")
    evals["gemini"] = Tier1Eval(
        provider="gemini",
        credential_source=source_to_credential_source(gemini_src),
        binary_path=None,
        sdk_installed=gemini_sdk,
        missing_prereqs=gemini_missing,
        missing_driver=not gemini_sdk,
        api_key=gemini_val,
    )
    return evals


def _as_optional_str(val: Any) -> str | None:
    return val if isinstance(val, str) and val != "" else None


def merge_tier1_with_cache(
    evals: dict[HarnessProvider, Tier1Eval],
    cache: dict[str, dict[str, Any]],
    active_runs: dict[HarnessProvider, str],
) -> ReadinessResponse:
    """Merge Tier-1 evaluations, cached outcomes, and active-run overlays into ReadinessResponse."""
    base_statuses: dict[HarnessProvider, ReadinessStatus] = {}
    for prov in ALL_PROVIDERS:
        ev = evals[prov]
        if ev.missing_prereqs:
            base_statuses[prov] = "MISSING_PREREQ"
        else:
            cached_status = cache.get(prov, {}).get("status")
            if cached_status in ("VERIFIED", "AUTH_FAILED"):
                base_statuses[prov] = cast(ReadinessStatus, cached_status)
            else:
                base_statuses[prov] = "CONFIGURED_UNVERIFIED"

    records: list[ProviderReadinessRecord] = []
    for prov in ALL_PROVIDERS:
        ev = evals[prov]
        status = base_statuses[prov]
        entry = cache.get(prov, {})
        active_id = active_runs.get(prov)
        last_verified_at: str | None = None
        last_auth_error_at: str | None = None
        last_auth_error_summary: str | None = None
        remediation = None

        if status == "MISSING_PREREQ":
            trigger = (
                ErrorCode.DRIVER_MISSING if ev.missing_driver else ErrorCode.AGENT_NOT_CONFIGURED
            )
            remediation = build_remediation(
                trigger,
                provider=prov,
                provider_statuses=base_statuses,
                credential_source=ev.credential_source,
            )
        elif status == "VERIFIED":
            last_verified_at = _as_optional_str(entry.get("last_verified_at"))
        elif status == "AUTH_FAILED":
            last_auth_error_at = _as_optional_str(entry.get("last_auth_error_at"))
            last_auth_error_summary = _as_optional_str(entry.get("last_auth_error_summary"))
            remediation = build_remediation(
                ErrorCode.AGENT_NOT_CONFIGURED,
                provider=prov,
                provider_statuses=base_statuses,
                credential_source=ev.credential_source,
            )

        records.append(
            ProviderReadinessRecord(
                provider=prov,
                status=status,
                credential_source=ev.credential_source,
                binary_path=ev.binary_path,
                sdk_installed=ev.sdk_installed,
                missing_prereqs=list(ev.missing_prereqs),
                available_models=default_model_options(prov),
                last_verified_at=last_verified_at,
                last_auth_error_at=last_auth_error_at,
                last_auth_error_summary=last_auth_error_summary,
                active_run=active_id is not None,
                active_run_id=active_id,
                remediation=remediation,
            )
        )
    return ReadinessResponse(providers=records)


__all__ = [
    "ALL_PROVIDERS",
    "CredentialSource",
    "ReadinessStatus",
    "Tier1Eval",
    "evaluate_tier1",
    "merge_tier1_with_cache",
    "scan_active_provider_runs",
    "source_to_credential_source",
]
