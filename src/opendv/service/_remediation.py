"""Deterministic Layer-4 ErrorRemediation builder covering all 34 ErrorCodes."""

from __future__ import annotations

import importlib.util
import shutil
from collections.abc import Mapping, Sequence
from typing import Literal

from opendv.models.errors import ErrorCode, ErrorRemediation, RemediationAction
from opendv.models.project import HarnessProvider

_SWITCH_PROVIDER_ORDER: tuple[HarnessProvider, ...] = (
    "claude-cli",
    "agy",
    "claude",
    "gemini",
)


def _switch_provider_action(
    provider: HarnessProvider,
    provider_statuses: Mapping[HarnessProvider, str] | None,
) -> RemediationAction | None:
    if provider_statuses is None:
        return None
    target: HarnessProvider | None = next(
        (
            cand
            for cand in _SWITCH_PROVIDER_ORDER
            if cand != provider
            and provider_statuses.get(cand) in ("VERIFIED", "CONFIGURED_UNVERIFIED")
        ),
        None,
    )
    if target is None:
        target = "claude-cli" if provider != "claude-cli" else "agy"
    return RemediationAction(
        actor="agent",
        label=f"Switch harness provider to {target}",
        command=f"uv run opendv settings set --harness-provider {target}",
        requires_tty=False,
    )


def _append_switch(
    actions: list[RemediationAction],
    provider: HarnessProvider,
    provider_statuses: Mapping[HarnessProvider, str] | None,
) -> list[RemediationAction]:
    switch_action = _switch_provider_action(provider, provider_statuses)
    if switch_action is not None:
        actions.append(switch_action)
    return actions


def build_remediation(
    trigger: ErrorCode,
    *,
    provider: HarnessProvider | None = None,
    provider_statuses: Mapping[HarnessProvider, str] | None = None,
    credential_source: Literal["cli_login", "store", "os_env", "none"] | None = None,
    missing_secrets: Sequence[str] = (),
    active_run_id: str | None = None,
    workspace_root: str | None = None,
    extra_name: str = "databricks",
    tesseract_missing: bool = False,
) -> ErrorRemediation:
    """Build deterministic agent-vs-human remediation guidance for a given ErrorCode."""
    if trigger == ErrorCode.DRIVER_MISSING:
        if provider in ("claude", "gemini"):
            actions = [
                RemediationAction(
                    actor="agent",
                    label="Install optional SDK extra",
                    command="uv sync --extra sdk",
                    requires_tty=False,
                )
            ]
            return ErrorRemediation(
                summary=(
                    f"Install optional SDK extra for '{provider}' or switch harness provider."
                ),
                actions=_append_switch(actions, provider, provider_statuses),
            )
        if provider in ("agy", "claude-cli"):
            actions = [
                RemediationAction(
                    actor="human",
                    label=f"Install '{provider}' CLI binary on PATH",
                    command=None,
                    requires_tty=True,
                )
            ]
            return ErrorRemediation(
                summary=(
                    f"Install missing '{provider}' CLI binary on PATH or switch harness provider."
                ),
                actions=_append_switch(actions, provider, provider_statuses),
            )
        if extra_name == "ocr":
            if tesseract_missing or (
                importlib.util.find_spec("pytesseract") is not None
                and shutil.which("tesseract") is None
            ):
                return ErrorRemediation(
                    summary=(
                        "Install system Tesseract OCR binary and ensure 'tesseract' is on PATH."
                    ),
                    actions=[
                        RemediationAction(
                            actor="human",
                            label="Install Tesseract OCR binary on PATH",
                            command=None,
                            requires_tty=True,
                        )
                    ],
                )
            return ErrorRemediation(
                summary="Install optional 'ocr' extra required for OCR processing.",
                actions=[
                    RemediationAction(
                        actor="agent",
                        label="Install optional OCR extra",
                        command="uv sync --extra ocr",
                        requires_tty=False,
                    )
                ],
            )
        return ErrorRemediation(
            summary="Install optional 'databricks' extra required for this operation.",
            actions=[
                RemediationAction(
                    actor="agent",
                    label="Install optional Databricks extra",
                    command="uv sync --extra databricks",
                    requires_tty=False,
                )
            ],
        )

    if trigger == ErrorCode.NEEDS_LOCAL_SECRET_BINDING:
        keys = [k.strip() for k in missing_secrets if k and k.strip()]
        if keys:
            return ErrorRemediation(
                summary=f"Bind required local secret(s): {', '.join(keys)}.",
                actions=[
                    RemediationAction(
                        actor="human",
                        label=f"Bind secret {k}",
                        command=f"uv run opendv secrets set {k} --prompt",
                        requires_tty=True,
                    )
                    for k in keys
                ],
            )
        return ErrorRemediation(
            summary="Bind required local secret(s) before retrying.",
            actions=[
                RemediationAction(
                    actor="human",
                    label="Bind required secret in local KeyringSecretStore",
                    command=None,
                    requires_tty=True,
                )
            ],
        )

    if trigger == ErrorCode.AGENT_NOT_CONFIGURED:
        if provider in ("claude", "gemini"):
            secret_key = "ANTHROPIC_API_KEY" if provider == "claude" else "GEMINI_API_KEY"
            note = " (GOOGLE_API_KEY is also accepted)" if provider == "gemini" else ""
            actions = [
                RemediationAction(
                    actor="human",
                    label=f"Bind {secret_key}",
                    command=f"uv run opendv secrets set {secret_key} --prompt",
                    requires_tty=True,
                )
            ]
            return ErrorRemediation(
                summary=(
                    f"Configure a valid {secret_key}{note} for '{provider}' "
                    "or switch harness provider."
                ),
                actions=_append_switch(actions, provider, provider_statuses),
            )
        if provider == "claude-cli" and credential_source in ("store", "os_env"):
            override_action = (
                RemediationAction(
                    actor="human",
                    label="Delete stored ANTHROPIC_API_KEY override",
                    command="uv run opendv secrets delete ANTHROPIC_API_KEY",
                    requires_tty=False,
                )
                if credential_source == "store"
                else RemediationAction(
                    actor="human",
                    label="Unset ANTHROPIC_API_KEY in shell and restart",
                    command=None,
                    requires_tty=False,
                )
            )
            return ErrorRemediation(
                summary="Remove stale ANTHROPIC_API_KEY override or sign in interactively.",
                actions=[
                    override_action,
                    RemediationAction(
                        actor="human",
                        label="Sign in to claude-cli",
                        command="claude auth login",
                        requires_tty=True,
                    ),
                ],
            )
        if provider in ("agy", "claude-cli"):
            login_cmd = "claude auth login" if provider == "claude-cli" else "agy"
            actions = [
                RemediationAction(
                    actor="human",
                    label=f"Sign in to {provider}",
                    command=login_cmd,
                    requires_tty=True,
                )
            ]
            return ErrorRemediation(
                summary=f"Sign in to '{provider}' CLI harness or switch harness provider.",
                actions=_append_switch(actions, provider, provider_statuses),
            )

    if trigger == ErrorCode.UNTRUSTED_WORKSPACE_OVERRIDE:
        cmd = (
            f"uv run opendv settings trust-workspace --path {workspace_root}"
            if workspace_root
            else None
        )
        return ErrorRemediation(
            summary="Workspace .opendv/agents/ overrides require explicit trust before loading.",
            actions=[
                RemediationAction(
                    actor="human",
                    label="Trust workspace overrides",
                    command=cmd,
                    requires_tty=False,
                )
            ],
        )

    if trigger == ErrorCode.PROJECT_JOB_ACTIVE:
        msg = (
            f"Wait for active run '{active_run_id}' to complete before retrying."
            if active_run_id
            else "Wait for the in-progress probe to complete before retrying."
        )
        return ErrorRemediation(summary=msg, actions=[])

    return ErrorRemediation(summary="Inspect ProblemDetails.detail and retry.", actions=[])


__all__ = ["build_remediation"]
