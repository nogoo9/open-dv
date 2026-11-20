"""Provider readiness probing and web bundle detection helpers for PlatformConfigStateMixin."""

from __future__ import annotations

import copy
import importlib.util
import os
import shutil
from pathlib import Path
from typing import Any, Literal

from opendv_agent_harness import HarnessSetupConfig, resolve_provider_model
from opendv_contracts.dtos.platform_config import ProviderReadinessDTO

_REGISTERED_PROVIDERS: tuple[
    Literal["agy", "agy-cli", "antigravity", "claude", "claude-cli", "claude-code"],
    ...,
] = (
    "agy",
    "agy-cli",
    "antigravity",
    "claude",
    "claude-cli",
    "claude-code",
)

_CANONICAL_PERSONAS: tuple[str, ...] = (
    "vault-first-mate",
    "scout-source",
    "scout-unstructured",
    "ship-vault",
)


def is_module_available(*module_names: str) -> bool:
    """Return True if any of `module_names` can be located via `importlib.util.find_spec`."""
    for mod_name in module_names:
        try:
            if importlib.util.find_spec(mod_name) is not None:
                return True
        except (ModuleNotFoundError, ValueError):
            continue
    return False


def is_web_ui_bundled(repo_root: Path) -> bool:
    """Check whether the compiled static Web UI bundle (`index.html`) is available."""
    env_static = os.environ.get("OPENDV_STATIC_DIR", "").strip()
    if env_static and (Path(env_static) / "index.html").is_file():
        return True
    repo_static = (
        repo_root
        / "apps"
        / "opendv-platform-api"
        / "src"
        / "opendv_platform_api"
        / "static"
        / "index.html"
    )
    if repo_static.is_file():
        return True
    try:
        spec = importlib.util.find_spec("opendv_platform_api")
        if spec is not None and spec.origin is not None:
            pkg_static = Path(spec.origin).resolve().parent / "static" / "index.html"
            if pkg_static.is_file():
                return True
    except (ModuleNotFoundError, ValueError):
        pass
    return False


def probe_providers_readiness_list(setup: HarnessSetupConfig) -> list[ProviderReadinessDTO]:
    """Probe binary availability, Python SDK installation, and env var presence."""
    providers_list: list[ProviderReadinessDTO] = []
    for provider in _REGISTERED_PROVIDERS:
        is_cli = provider in {"agy", "agy-cli", "claude-cli", "claude-code"}
        execution_mode: Literal["cli", "sdk"] = "cli" if is_cli else "sdk"
        is_claude_family = provider in {"claude", "claude-cli", "claude-code"}

        binary_name: str | None = None
        if provider in {"agy", "agy-cli"}:
            binary_name = "agy"
        elif provider in {"claude-cli", "claude-code"}:
            binary_name = "claude"

        binary_found = bool(shutil.which(binary_name)) if binary_name is not None else False

        sdk_installed = False
        if provider == "antigravity":
            sdk_installed = is_module_available("google.antigravity", "google.genai")
        elif provider == "claude":
            sdk_installed = is_module_available("claude_agent_sdk", "anthropic")

        active_is_same_family = (
            setup.provider in {"claude", "claude-cli", "claude-code"}
        ) == is_claude_family
        configured_env_var = (
            setup.auth.api_key_env_var
            if (active_is_same_family and setup.auth.api_key_env_var)
            else None
        )
        api_key_env_var = configured_env_var or (
            "ANTHROPIC_API_KEY" if is_claude_family else "GEMINI_API_KEY"
        )

        env_key_present = bool(os.environ.get(api_key_env_var, "").strip())
        credentials_present = env_key_present or (execution_mode == "cli" and binary_found)
        available = binary_found if execution_mode == "cli" else (sdk_installed and env_key_present)

        if setup.provider == provider and setup.auth.auth_mode != "none":
            auth_mode = setup.auth.auth_mode
        else:
            auth_mode = "cli_session" if execution_mode == "cli" else "api_key"

        if provider in {"claude-cli", "claude-code"}:
            cli_overrides = HarnessSetupConfig(provider=provider).model_overrides  # type: ignore[arg-type]
            default_reasoning = resolve_provider_model(
                provider, "reasoning", overrides=cli_overrides
            )
            default_fast = resolve_provider_model(provider, "fast", overrides=cli_overrides)
        else:
            default_reasoning = resolve_provider_model(provider, "reasoning")
            default_fast = resolve_provider_model(provider, "fast")

        if execution_mode == "cli":
            status_message = (
                f"CLI binary '{binary_name}' found on PATH."
                if binary_found
                else f"CLI binary '{binary_name}' not found on PATH."
            )
        elif available:
            status_message = (
                f"SDK installed and '{api_key_env_var}' credential environment variable set."
            )
        elif not sdk_installed:
            status_message = f"Python SDK for '{provider}' is not installed."
        else:
            status_message = (
                f"SDK installed but '{api_key_env_var}' environment variable is not set."
            )

        providers_list.append(
            ProviderReadinessDTO(
                provider=provider,
                execution_mode=execution_mode,
                available=available,
                binary_name=binary_name,
                binary_found=binary_found,
                sdk_installed=sdk_installed,
                auth_mode=auth_mode,
                api_key_env_var=api_key_env_var,
                credentials_present=credentials_present,
                default_reasoning_model=default_reasoning,
                default_fast_model=default_fast,
                status_message=status_message,
            )
        )
    return providers_list


def is_provider_ready(setup: HarnessSetupConfig) -> tuple[bool, ProviderReadinessDTO | None]:
    """Return `(ready, matching_dto)` for the active provider in `setup`."""
    probed = probe_providers_readiness_list(setup)
    for dto in probed:
        if dto.provider == setup.provider:
            return dto.available, dto
    return False, None


def find_any_ready_provider(setup: HarnessSetupConfig | None = None) -> ProviderReadinessDTO | None:
    """Return the first available provider DTO if any adapter is configured & ready."""
    eff_setup = setup or HarnessSetupConfig()
    for dto in probe_providers_readiness_list(eff_setup):
        if dto.available:
            return dto
    return None


def deep_merge_harness_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge `override` harness configuration dictionary onto `base`."""
    merged = copy.deepcopy(base)
    old_provider = merged.get("provider", "agy")
    new_provider = override.get("provider", old_provider)

    if new_provider != old_provider and "model_overrides" not in override:
        merged.pop("model_overrides", None)

    for key, val in override.items():
        if (
            key in {"auth", "budget", "sandbox", "extra_provider_options"}
            and isinstance(val, dict)
            and isinstance(merged.get(key), dict)
        ):
            merged[key] = deep_merge_harness_dict(merged[key], val)
        elif key in {"model_overrides", "agent_models"} and isinstance(val, dict):
            merged[key] = dict(val)
        else:
            merged[key] = copy.deepcopy(val)
    return merged


__all__ = [
    "_CANONICAL_PERSONAS",
    "_REGISTERED_PROVIDERS",
    "deep_merge_harness_dict",
    "find_any_ready_provider",
    "is_module_available",
    "is_provider_ready",
    "is_web_ui_bundled",
    "probe_providers_readiness_list",
]
