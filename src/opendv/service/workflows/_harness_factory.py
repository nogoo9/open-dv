"""Synchronous harness resolution, readiness wrapping, and bounded lease TTL sizing."""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Mapping

from opendv.agents import (
    AgentHarnessPort,
    SdkHarnessRunner,
    SubprocessHarnessRunner,
    SubprocessStreamFn,
    resolve_cli_binary,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.policy import HarnessBudget
from opendv.models.project import HarnessProvider, ProjectSettingsSnapshot, WorkspaceSettings
from opendv.models.wire_dtos_discovery import RunStateResp
from opendv.service._remediation import build_remediation
from opendv.service._secret_manifest import SecretSource
from opendv.service.readiness import (
    CredentialSource,
    HarnessReadinessService,
    ReadinessRecordingHarness,
)
from opendv.service.secrets import KeyringSecretStore


def compute_job_lease_ttl(max_turns: int | None, timeout_seconds: int | None) -> int:
    """Compute bounded job lease TTL in seconds covering max_turns + V7/V8/scouts."""
    turns = max(1, max_turns or 1)
    per_turn = max(1, timeout_seconds or 300)
    return min(86_400, max(120, (turns + 4) * per_turn + 60))


def coalesce_snapshot_budget(
    base: HarnessBudget, snap: ProjectSettingsSnapshot
) -> HarnessBudget:
    """Override base HarnessBudget fields with any non-None ProjectSettingsSnapshot limits."""
    return HarnessBudget(
        max_turns=snap.max_turns if snap.max_turns is not None else base.max_turns,
        max_tokens=snap.max_tokens if snap.max_tokens is not None else base.max_tokens,
        timeout_seconds=(
            snap.timeout_seconds if snap.timeout_seconds is not None else base.timeout_seconds
        ),
    )


def coalesce_prior_state_budget(
    base: HarnessBudget, prior: RunStateResp | None
) -> HarnessBudget:
    """Coalesce prior RunStateResp effective budget limits onto base HarnessBudget."""
    if prior is None:
        return base
    return HarnessBudget(
        max_turns=prior.effective_max_turns or base.max_turns,
        max_tokens=prior.effective_max_tokens or base.max_tokens,
        timeout_seconds=prior.effective_timeout_seconds or base.timeout_seconds,
    )


def _cred_source(src: SecretSource) -> CredentialSource:
    if src in ("keyring", "user_state_file"):
        return "store"
    if src == "env":
        return "os_env"
    return "none"


def _statuses_or_none(
    readiness_service: HarnessReadinessService | None,
) -> dict[HarnessProvider, str] | None:
    return readiness_service.get_provider_statuses() if readiness_service is not None else None


def resolve_harness(
    settings: WorkspaceSettings | ProjectSettingsSnapshot,
    secret_store: KeyringSecretStore,
    *,
    injected_harness: AgentHarnessPort | None = None,
    model_overrides: Mapping[str, str] | None = None,
    stream_fn: SubprocessStreamFn | None = None,
    readiness_service: HarnessReadinessService | None = None,
    which_fn: Callable[[str], str | None] | None = None,
    find_spec_fn: Callable[[str], object | None] | None = None,
) -> AgentHarnessPort:
    """Resolve AgentHarnessPort synchronously and optionally wrap with ReadinessRecordingHarness."""
    if injected_harness is not None:
        return injected_harness

    base_ovr = getattr(settings, "model_overrides", None)
    merged_ovr: dict[str, str] = dict(base_ovr) if isinstance(base_ovr, Mapping) else {}
    if model_overrides:
        merged_ovr.update(model_overrides)

    parent_env = secret_store.effective_env()
    eff_which = (
        which_fn
        if which_fn is not None
        else (readiness_service.which_fn if readiness_service is not None else None)
    )
    eff_find_spec = (
        find_spec_fn
        if find_spec_fn is not None
        else (
            readiness_service.find_spec_fn
            if readiness_service is not None and readiness_service.find_spec_fn is not None
            else importlib.util.find_spec
        )
    )

    provider = settings.harness_provider
    runner: AgentHarnessPort
    cred_src: CredentialSource

    if provider in ("agy", "claude-cli"):
        try:
            resolved_bin = resolve_cli_binary(provider, env=parent_env, which_fn=eff_which)
        except OpenDVError as exc:
            remediation = exc.remediation or build_remediation(
                ErrorCode.DRIVER_MISSING,
                provider=provider,
                provider_statuses=_statuses_or_none(readiness_service),
                credential_source="none",
            )
            raise OpenDVError(
                exc.detail,
                error_code=exc.error_code,
                remediation=remediation,
            ) from exc

        if provider == "claude-cli":
            cli_api_key, ant_src = secret_store.get_secret_with_source(
                "ANTHROPIC_API_KEY",
                project_precedence=settings.secret_precedence,
            )
            cred_src = _cred_source(ant_src) if cli_api_key is not None else "cli_login"
        else:
            cli_api_key = None
            cred_src = "cli_login"

        runner = SubprocessHarnessRunner(
            provider=provider,
            binary_path=resolved_bin,
            api_key=cli_api_key,
            model_overrides=merged_ovr,
            env=parent_env,
            stream_fn=stream_fn,
            which_fn=eff_which,
        )
    elif provider == "claude":
        if eff_find_spec("anthropic") is None:
            raise OpenDVError(
                "Optional SDK package 'anthropic' for provider 'claude' is not installed.",
                error_code=ErrorCode.DRIVER_MISSING,
                remediation=build_remediation(
                    ErrorCode.DRIVER_MISSING,
                    provider="claude",
                    provider_statuses=_statuses_or_none(readiness_service),
                    credential_source="none",
                ),
            )
        api_key, src = secret_store.get_secret_with_source(
            "ANTHROPIC_API_KEY",
            project_precedence=settings.secret_precedence,
        )
        cred_src = _cred_source(src)
        if not api_key:
            raise OpenDVError(
                "Provider 'claude' requires ANTHROPIC_API_KEY in secret store or environment.",
                error_code=ErrorCode.AGENT_NOT_CONFIGURED,
                remediation=build_remediation(
                    ErrorCode.AGENT_NOT_CONFIGURED,
                    provider="claude",
                    provider_statuses=_statuses_or_none(readiness_service),
                    credential_source=cred_src,
                ),
            )
        runner = SdkHarnessRunner(
            provider="claude",
            api_key=api_key,
            env=parent_env,
            model_overrides=merged_ovr,
        )
    elif provider == "gemini":
        if eff_find_spec("google") is None or eff_find_spec("google.genai") is None:
            raise OpenDVError(
                "Optional SDK package 'google-genai' for provider 'gemini' is not installed.",
                error_code=ErrorCode.DRIVER_MISSING,
                remediation=build_remediation(
                    ErrorCode.DRIVER_MISSING,
                    provider="gemini",
                    provider_statuses=_statuses_or_none(readiness_service),
                    credential_source="none",
                ),
            )
        api_key, src = secret_store.resolve_gemini_api_key_with_source(
            project_precedence=settings.secret_precedence,
        )
        cred_src = _cred_source(src)
        if not api_key:
            raise OpenDVError(
                "Provider 'gemini' requires GEMINI_API_KEY or GOOGLE_API_KEY.",
                error_code=ErrorCode.AGENT_NOT_CONFIGURED,
                remediation=build_remediation(
                    ErrorCode.AGENT_NOT_CONFIGURED,
                    provider="gemini",
                    provider_statuses=_statuses_or_none(readiness_service),
                    credential_source=cred_src,
                ),
            )
        runner = SdkHarnessRunner(
            provider="gemini",
            api_key=api_key,
            env=parent_env,
            model_overrides=merged_ovr,
        )
    else:
        raise OpenDVError(
            f"Unsupported harness provider '{provider}'.",
            error_code=ErrorCode.AGENT_NOT_CONFIGURED,
        )

    if readiness_service is not None:
        return ReadinessRecordingHarness(
            runner,
            readiness_service=readiness_service,
            credential_source=cred_src,
            record_to_disk=readiness_service.should_record_to_disk(
                project_precedence=settings.secret_precedence
            ),
        )
    return runner


__all__ = [
    "coalesce_prior_state_budget",
    "coalesce_snapshot_budget",
    "compute_job_lease_ttl",
    "resolve_harness",
]
