"""Two-tier harness readiness diagnostics, minimal 4-field cache, and passive turn wrapper."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import httpx

from opendv.agents import (
    AgentHarnessPort,
    AgentTurnRequest,
    AgentTurnResult,
    ModuleLoaderFn,
    ProbeFn,
    RunEventSink,
    ScoutToolDispatcher,
    SdkClientFactory,
    SubprocessExecFn,
    Tier2ProbeOutcome,
    default_model_options,
    probe_agy,
    probe_claude_cli,
    probe_claude_sdk,
    probe_gemini_sdk,
)
from opendv.agents._pii_redact import redact_telemetry_text
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.project import HarnessProvider, SecretPrecedence
from opendv.models.wire_dtos_readiness import ProviderReadinessRecord, ReadinessResponse
from opendv.service._readiness_tier1 import (
    CredentialSource,
    ReadinessStatus,
    Tier1Eval,
    evaluate_tier1,
    merge_tier1_with_cache,
    scan_active_provider_runs,
)
from opendv.service._remediation import build_remediation
from opendv.service._state_records import utc_now
from opendv.service.os_platform import atomic_write_text
from opendv.service.secrets import KeyringSecretStore
from opendv.service.settings_store import SettingsStore

_LOGGER = logging.getLogger(__name__)
_SECRET_TO_PROVIDERS: dict[str, tuple[HarnessProvider, ...]] = {
    "ANTHROPIC_API_KEY": ("claude", "claude-cli"),
    "GEMINI_API_KEY": ("gemini",),
    "GOOGLE_API_KEY": ("gemini",),
}


class HarnessReadinessService:
    """Two-tier harness readiness evaluator, best-effort cache, and probe coordinator."""

    def __init__(
        self,
        *,
        settings_store: SettingsStore,
        secret_store: KeyringSecretStore,
        db_path: Path,
        readiness_path: Path | None = None,
        which_fn: Callable[[str], str | None] | None = None,
        find_spec_fn: Callable[[str], object | None] | None = None,
        probe_fn: ProbeFn | None = None,
        transport: httpx.BaseTransport | None = None,
        subprocess_exec_fn: SubprocessExecFn | None = None,
        sdk_client_factory: SdkClientFactory | None = None,
        module_loader: ModuleLoaderFn | None = None,
    ) -> None:
        self._settings = settings_store
        self._secrets = secret_store
        self._db_path = Path(db_path)
        self._readiness_path = (
            Path(readiness_path)
            if readiness_path is not None
            else settings_store.state_dir / "readiness.json"
        )
        self._which_fn = which_fn
        self._find_spec_fn = find_spec_fn
        self._probe_fn = probe_fn
        self._transport = transport
        self._subprocess_exec_fn = subprocess_exec_fn
        self._sdk_client_factory = sdk_client_factory
        self._module_loader = module_loader
        self._lock: threading.Lock = threading.Lock()
        self._probing: set[HarnessProvider] = set()

    @property
    def which_fn(self) -> Callable[[str], str | None] | None:
        return self._which_fn

    @property
    def find_spec_fn(self) -> Callable[[str], object | None] | None:
        return self._find_spec_fn

    def _read_cache_best_effort(self) -> dict[str, dict[str, Any]]:
        try:
            raw = self._readiness_path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
        except (OSError, ValueError):
            return {}
        if not isinstance(parsed, dict):
            return {}
        providers = parsed.get("providers")
        if not isinstance(providers, dict):
            return {}
        return {
            str(k): dict(v)
            for k, v in providers.items()
            if isinstance(k, str) and isinstance(v, dict)
        }

    def _write_cache_under_lock(
        self, mutator: Callable[[dict[str, dict[str, Any]]], bool]
    ) -> bool:
        providers: dict[str, dict[str, Any]] = {}
        if self._readiness_path.exists():
            try:
                raw = self._readiness_path.read_text(encoding="utf-8")
            except OSError as exc:
                _LOGGER.warning("Skipping readiness.json update after read error: %s", exc)
                return False
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = {}
            if isinstance(parsed, dict) and isinstance(parsed.get("providers"), dict):
                providers = {
                    str(k): dict(v)
                    for k, v in parsed["providers"].items()
                    if isinstance(k, str) and isinstance(v, dict)
                }
        if not mutator(providers):
            return True
        payload = {"schema_version": "2.0", "providers": providers}
        try:
            atomic_write_text(
                self._readiness_path,
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
            )
        except (OSError, OpenDVError) as exc:
            _LOGGER.warning("Best-effort readiness.json write failed: %s", exc)
            return False
        return True

    def should_record_to_disk(self, *, project_precedence: SecretPrecedence | None = None) -> bool:
        """Return True only when user-global default precedence matches effective precedence."""
        if self._secrets.is_env_first_override_active():
            return False
        try:
            eff_prec = self._secrets.resolve_precedence(project_precedence=project_precedence)
        except OpenDVError:
            return False
        return eff_prec == self._settings.load_settings().secret_precedence

    def _evaluate_all(self) -> tuple[ReadinessResponse, dict[HarnessProvider, Tier1Eval]]:
        evals = evaluate_tier1(
            self._secrets,
            which_fn=self._which_fn,
            find_spec_fn=self._find_spec_fn,
        )
        cache = self._read_cache_best_effort()
        active_runs = scan_active_provider_runs(self._db_path)
        return merge_tier1_with_cache(evals, cache, active_runs), evals

    def get_readiness(self) -> ReadinessResponse:
        """Evaluate fast Tier-1 prerequisites merged with persisted readiness cache."""
        resp, _ = self._evaluate_all()
        return resp

    def get_provider_statuses(self) -> dict[HarnessProvider, str]:
        """Return {provider: status} map from current Tier-1 + cache evaluation."""
        return {rec.provider: rec.status for rec in self.get_readiness().providers}

    def record_turn_success(
        self,
        provider: HarnessProvider,
        *,
        record_to_disk: bool = True,
    ) -> None:
        """Persist VERIFIED status after a completed real turn."""
        if not record_to_disk:
            return
        now_iso = utc_now().isoformat()

        def _mut(providers: dict[str, dict[str, Any]]) -> bool:
            providers[provider] = {
                "status": "VERIFIED",
                "last_verified_at": now_iso,
                "last_auth_error_at": None,
                "last_auth_error_summary": None,
            }
            return True

        with self._lock:
            self._write_cache_under_lock(_mut)

    def record_turn_auth_failure(
        self,
        provider: HarnessProvider,
        error_text: str,
        *,
        record_to_disk: bool = True,
    ) -> dict[HarnessProvider, str]:
        """Persist AUTH_FAILED status on auth failure and return updated provider statuses."""
        if record_to_disk:
            now_iso = utc_now().isoformat()
            summary = redact_telemetry_text(error_text)[:300]

            def _mut(providers: dict[str, dict[str, Any]]) -> bool:
                providers[provider] = {
                    "status": "AUTH_FAILED",
                    "last_verified_at": None,
                    "last_auth_error_at": now_iso,
                    "last_auth_error_summary": summary,
                }
                return True

            with self._lock:
                self._write_cache_under_lock(_mut)
        return self.get_provider_statuses()

    def clear_for_secret(self, key: str) -> None:
        """Clear cached verification state for providers bound to key."""
        targets = _SECRET_TO_PROVIDERS.get(key.strip())
        if not targets:
            return

        def _mut(providers: dict[str, dict[str, Any]]) -> bool:
            changed = False
            for t in targets:
                if t in providers:
                    del providers[t]
                    changed = True
            return changed

        with self._lock:
            if not self._readiness_path.exists():
                return
            self._write_cache_under_lock(_mut)

    def clear_all(self) -> None:
        """Clear all cached provider verification state (e.g., precedence/backend changed)."""

        def _mut(providers: dict[str, dict[str, Any]]) -> bool:
            if not providers:
                return False
            providers.clear()
            return True

        with self._lock:
            if not self._readiness_path.exists():
                return
            self._write_cache_under_lock(_mut)

    def _prepare_probe(
        self, provider: HarnessProvider
    ) -> tuple[bool, Tier1Eval, ProviderReadinessRecord, dict[HarnessProvider, str]]:
        active_runs = scan_active_provider_runs(self._db_path)
        if provider in active_runs:
            active_id = active_runs[provider]
            raise OpenDVError(
                f"Cannot probe provider '{provider}' while run '{active_id}' is active.",
                error_code=ErrorCode.PROJECT_JOB_ACTIVE,
                remediation=build_remediation(
                    ErrorCode.PROJECT_JOB_ACTIVE,
                    provider=provider,
                    active_run_id=active_id,
                ),
            )
        record_to_disk = self.should_record_to_disk()
        resp, evals = self._evaluate_all()
        rec = next(r for r in resp.providers if r.provider == provider)
        statuses: dict[HarnessProvider, str] = {r.provider: r.status for r in resp.providers}
        return record_to_disk, evals[provider], rec, statuses

    def _build_probed_record(
        self,
        ev: Tier1Eval,
        provider_statuses: dict[HarnessProvider, str],
        *,
        outcome_kind: Literal["verified", "auth_failed"],
        now_iso: str,
        err_summary: str | None,
    ) -> ProviderReadinessRecord:
        prov = ev.provider
        if outcome_kind == "verified":
            return ProviderReadinessRecord(
                provider=prov,
                status="VERIFIED",
                credential_source=ev.credential_source,
                binary_path=ev.binary_path,
                sdk_installed=ev.sdk_installed,
                missing_prereqs=list(ev.missing_prereqs),
                available_models=default_model_options(prov),
                last_verified_at=now_iso,
                last_auth_error_at=None,
                last_auth_error_summary=None,
                active_run=False,
                active_run_id=None,
                remediation=None,
            )
        updated_statuses = dict(provider_statuses)
        updated_statuses[prov] = "AUTH_FAILED"
        return ProviderReadinessRecord(
            provider=prov,
            status="AUTH_FAILED",
            credential_source=ev.credential_source,
            binary_path=ev.binary_path,
            sdk_installed=ev.sdk_installed,
            missing_prereqs=list(ev.missing_prereqs),
            available_models=default_model_options(prov),
            last_verified_at=None,
            last_auth_error_at=now_iso,
            last_auth_error_summary=err_summary,
            active_run=False,
            active_run_id=None,
            remediation=build_remediation(
                ErrorCode.AGENT_NOT_CONFIGURED,
                provider=prov,
                provider_statuses=updated_statuses,
                credential_source=ev.credential_source,
            ),
        )

    def _build_transient_record(
        self,
        provider: HarnessProvider,
        error_summary: str | None,
    ) -> ProviderReadinessRecord:
        resp = self.get_readiness()
        rec = next(r for r in resp.providers if r.provider == provider)
        return rec.model_copy(
            update={
                "last_auth_error_at": utc_now().isoformat(),
                "last_auth_error_summary": redact_telemetry_text(error_summary or "")[:300],
            }
        )

    def _persist_probe_outcome(
        self,
        provider: HarnessProvider,
        ev: Tier1Eval,
        provider_statuses: dict[HarnessProvider, str],
        outcome_kind: Literal["verified", "auth_failed"],
        now_iso: str,
        err_summary: str | None,
    ) -> ProviderReadinessRecord:
        active_runs = scan_active_provider_runs(self._db_path)
        if provider in active_runs:
            resp = self.get_readiness()
            return next(r for r in resp.providers if r.provider == provider)

        entry = (
            {
                "status": "VERIFIED",
                "last_verified_at": now_iso,
                "last_auth_error_at": None,
                "last_auth_error_summary": None,
            }
            if outcome_kind == "verified"
            else {
                "status": "AUTH_FAILED",
                "last_verified_at": None,
                "last_auth_error_at": now_iso,
                "last_auth_error_summary": err_summary,
            }
        )

        def _set_entry(providers: dict[str, dict[str, Any]]) -> bool:
            providers[provider] = entry
            return True

        with self._lock:
            wrote = self._write_cache_under_lock(_set_entry)
        if wrote:
            resp = self.get_readiness()
            return next(r for r in resp.providers if r.provider == provider)
        return self._build_probed_record(
            ev,
            provider_statuses,
            outcome_kind=outcome_kind,
            now_iso=now_iso,
            err_summary=err_summary,
        )

    async def test_agent_readiness(self, provider: HarnessProvider) -> ProviderReadinessRecord:
        """Execute single-flight Tier-2 auth verification probe for provider."""
        with self._lock:
            if provider in self._probing:
                raise OpenDVError(
                    f"Readiness probe already in progress for provider '{provider}'.",
                    error_code=ErrorCode.PROJECT_JOB_ACTIVE,
                    remediation=build_remediation(
                        ErrorCode.PROJECT_JOB_ACTIVE,
                        provider=provider,
                        active_run_id=None,
                    ),
                )
            self._probing.add(provider)

        try:
            record_to_disk, ev, rec, statuses = await asyncio.to_thread(
                self._prepare_probe, provider
            )
            if rec.status == "MISSING_PREREQ":
                return rec

            parent_env = self._secrets.effective_env()
            outcome: Tier2ProbeOutcome
            if self._probe_fn is not None:
                outcome = await self._probe_fn(provider, ev.binary_path, ev.api_key, parent_env)
            elif provider == "claude":
                outcome = await probe_claude_sdk(
                    api_key=ev.api_key or "",
                    parent_env=parent_env,
                    sdk_client_factory=self._sdk_client_factory,
                    module_loader=self._module_loader,
                )
            elif provider == "gemini":
                outcome = await probe_gemini_sdk(
                    api_key=ev.api_key or "",
                    sdk_client_factory=self._sdk_client_factory,
                    module_loader=self._module_loader,
                )
            elif provider == "claude-cli":
                outcome = await probe_claude_cli(
                    binary_path=ev.binary_path or "claude",
                    api_key=ev.api_key,
                    parent_env=parent_env,
                    transport=self._transport,
                    subprocess_exec_fn=self._subprocess_exec_fn,
                )
            else:
                outcome = await probe_agy(
                    binary_path=ev.binary_path or "agy",
                    parent_env=parent_env,
                    subprocess_exec_fn=self._subprocess_exec_fn,
                )

            if outcome.kind == "transient_error":
                return await asyncio.to_thread(
                    self._build_transient_record, provider, outcome.error_summary
                )

            now_iso = utc_now().isoformat()
            err_summary = (
                redact_telemetry_text(outcome.error_summary or "")[:300]
                if outcome.kind == "auth_failed"
                else None
            )
            if not record_to_disk:
                return self._build_probed_record(
                    ev,
                    statuses,
                    outcome_kind=outcome.kind,
                    now_iso=now_iso,
                    err_summary=err_summary,
                )
            return await asyncio.to_thread(
                self._persist_probe_outcome,
                provider,
                ev,
                statuses,
                outcome.kind,
                now_iso,
                err_summary,
            )
        finally:
            with self._lock:
                self._probing.discard(provider)


class ReadinessRecordingHarness(AgentHarnessPort):
    """Passive AgentHarnessPort wrapper recording real-turn readiness and attaching remediations."""

    def __init__(
        self,
        inner: AgentHarnessPort,
        *,
        readiness_service: HarnessReadinessService,
        credential_source: CredentialSource | None = None,
        record_to_disk: bool = True,
    ) -> None:
        self._inner = inner
        self._readiness = readiness_service
        self._credential_source = credential_source
        self._record_to_disk = record_to_disk

    @property
    def provider_name(self) -> HarnessProvider:
        return self._inner.provider_name

    async def run_turn(
        self,
        request: AgentTurnRequest,
        *,
        event_sink: RunEventSink | None = None,
        scout_dispatcher: ScoutToolDispatcher | None = None,
    ) -> AgentTurnResult:
        try:
            result = await self._inner.run_turn(
                request,
                event_sink=event_sink,
                scout_dispatcher=scout_dispatcher,
            )
        except OpenDVError as exc:
            statuses: dict[HarnessProvider, str] | None = None
            if exc.error_code == ErrorCode.AGENT_NOT_CONFIGURED:
                try:
                    statuses = await asyncio.to_thread(
                        self._readiness.record_turn_auth_failure,
                        self.provider_name,
                        exc.detail,
                        record_to_disk=self._record_to_disk,
                    )
                except (OSError, ValueError, TypeError, RuntimeError, OpenDVError) as cache_exc:
                    _LOGGER.warning("Failed to record turn auth failure: %s", cache_exc)
            elif exc.error_code == ErrorCode.DRIVER_MISSING:
                try:
                    statuses = await asyncio.to_thread(self._readiness.get_provider_statuses)
                except (OSError, ValueError, TypeError, RuntimeError, OpenDVError) as cache_exc:
                    _LOGGER.warning("Failed to read provider statuses: %s", cache_exc)
            if exc.remediation is None:
                remediation = build_remediation(
                    exc.error_code,
                    provider=self.provider_name,
                    credential_source=self._credential_source,
                    provider_statuses=statuses,
                )
                raise OpenDVError(
                    exc.detail,
                    error_code=exc.error_code,
                    diagnostics=exc.diagnostics,
                    defect_report=exc.defect_report,
                    run_id=exc.run_id,
                    remediation=remediation,
                ) from exc
            raise

        try:
            await asyncio.to_thread(
                self._readiness.record_turn_success,
                self.provider_name,
                record_to_disk=self._record_to_disk,
            )
        except (OSError, ValueError, TypeError, RuntimeError, OpenDVError) as cache_exc:
            _LOGGER.warning("Failed to record turn success: %s", cache_exc)
        return result


__all__ = [
    "CredentialSource",
    "HarnessReadinessService",
    "ReadinessRecordingHarness",
    "ReadinessStatus",
]
