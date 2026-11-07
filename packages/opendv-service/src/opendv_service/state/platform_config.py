"""Platform & Agent Harness configuration state management and provider readiness probing."""

from __future__ import annotations

import copy
import importlib.util
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import ValidationError

from opendv_agent_harness import (
    HarnessSetupConfig,
    resolve_default_tier_for_persona,
    resolve_provider_model,
)
from opendv_contracts.dtos.platform_config import (
    ConfigValidationDiagnosticDTO,
    HarnessAuthConfigDTO,
    HarnessBudgetConfigDTO,
    HarnessConfigDTO,
    HarnessSandboxPolicyDTO,
    PlatformConfigResponse,
    PlatformConfigUpdateRequest,
    PlatformConfigValidateRequest,
    PlatformConfigValidationResponse,
    ProviderReadinessDTO,
)
from opendv_contracts.errors import DEFAULT_MAX_INLINE_BYTES
from opendv_service.settings import (
    DEFAULT_CORS_ORIGINS as _DEFAULT_CORS_ORIGINS,
)
from opendv_service.settings import (
    DEFAULT_SERVER_HOST,
    DEFAULT_SERVER_PORT,
)

if TYPE_CHECKING:
    from opendv_service.state.base import BaseStateContainer

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

_ALLOWED_ONTOLOGY_POLICIES: frozenset[str] = frozenset(
    {
        "off",
        "recommend",
        "auto_accept",
        "suggest_only",
        "steward_review",
        "auto_accept_above",
    }
)

_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_module_available(*module_names: str) -> bool:
    """Return True if any of `module_names` can be located via `importlib.util.find_spec`."""
    for mod_name in module_names:
        try:
            if importlib.util.find_spec(mod_name) is not None:
                return True
        except (ModuleNotFoundError, ValueError):
            continue
    return False


def _deep_merge_harness_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
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
            nested = dict(merged[key])
            nested.update(val)
            merged[key] = nested
        elif key in {"model_overrides", "agent_models"} and isinstance(val, dict):
            merged[key] = dict(val)
        else:
            merged[key] = copy.deepcopy(val)
    return merged


def _validate_and_normalize_harness(
    raw_harness: dict[str, Any],
) -> tuple[HarnessSetupConfig, HarnessConfigDTO]:
    """Validate a raw harness dictionary against `HarnessSetupConfig` and `HarnessConfigDTO`."""
    if not isinstance(raw_harness, dict):
        raise ValueError("Harness configuration must be a JSON/YAML mapping object.")

    setup_input = copy.deepcopy(raw_harness)
    extra_opts = dict(setup_input.get("extra_provider_options") or {})

    if "allow_cli_schema_omission_retry" in setup_input:
        raw_retry = setup_input.pop("allow_cli_schema_omission_retry")
        if not isinstance(raw_retry, bool):
            raise ValueError("allow_cli_schema_omission_retry must be a boolean.")
        extra_opts["allow_cli_schema_omission_retry"] = raw_retry

    sandbox_raw = setup_input.get("sandbox")
    if isinstance(sandbox_raw, dict) and "allow_network_access" in sandbox_raw:
        sandbox_copy = dict(sandbox_raw)
        raw_net = sandbox_copy.pop("allow_network_access")
        if not isinstance(raw_net, bool):
            raise ValueError("sandbox.allow_network_access must be a boolean.")
        extra_opts["allow_network_access"] = raw_net
        setup_input["sandbox"] = sandbox_copy

    if extra_opts:
        setup_input["extra_provider_options"] = extra_opts

    # Validate ontology_policy explicitly
    ont_pol = str(setup_input.get("ontology_policy", "off")).strip()
    if ont_pol not in _ALLOWED_ONTOLOGY_POLICIES and not ont_pol.startswith("auto_accept_above:"):
        raise ValueError(
            f"Invalid ontology_policy '{ont_pol}'; expected one of "
            f"{sorted(_ALLOWED_ONTOLOGY_POLICIES)}."
        )

    # Validate api_key_env_var is an env var name, never a raw secret
    auth_raw = setup_input.get("auth")
    if isinstance(auth_raw, dict):
        env_var = auth_raw.get("api_key_env_var")
        if env_var is not None and str(env_var).strip():
            env_var_str = str(env_var).strip()
            if not _ENV_VAR_NAME_RE.match(env_var_str):
                raise ValueError(
                    f"Invalid auth.api_key_env_var '{env_var_str}'; must be a valid environment "
                    "variable name (e.g. ANTHROPIC_API_KEY, GEMINI_API_KEY), never a raw secret."
                )

    provider_val = setup_input.get("provider", "agy")
    if provider_val == "claude" and "model_overrides" not in setup_input:
        setup_input["model_overrides"] = {
            "reasoning": resolve_provider_model("claude", "reasoning"),
            "fast": resolve_provider_model("claude", "fast"),
        }

    setup_obj = HarnessSetupConfig.model_validate(setup_input)

    allow_cli_retry = bool(
        setup_obj.extra_provider_options.get("allow_cli_schema_omission_retry", False)
    )
    allow_net_access = bool(setup_obj.extra_provider_options.get("allow_network_access", False))

    auth_dto = HarnessAuthConfigDTO(
        auth_mode=setup_obj.auth.auth_mode,
        mode=setup_obj.auth.mode,
        api_key_env_var=setup_obj.auth.api_key_env_var,
        api_base_url=setup_obj.auth.api_base_url,
        gcp_project=setup_obj.auth.gcp_project,
        gcp_location=setup_obj.auth.gcp_location,
        gcp_credentials_path=setup_obj.auth.gcp_credentials_path,
        aws_region=setup_obj.auth.aws_region,
        aws_profile=setup_obj.auth.aws_profile,
        require_credentials_present=setup_obj.auth.require_credentials_present,
        require_preflight=setup_obj.auth.require_preflight,
    )
    budget_dto = HarnessBudgetConfigDTO(
        max_turns=setup_obj.budget.max_turns,
        max_input_tokens=setup_obj.budget.max_input_tokens,
        max_output_tokens=setup_obj.budget.max_output_tokens,
        timeout_seconds=setup_obj.budget.timeout_seconds,
        max_cost_usd=setup_obj.budget.max_cost_usd,
    )
    sandbox_dto = HarnessSandboxPolicyDTO(
        read_only_workspace=setup_obj.sandbox.read_only_workspace,
        allow_shell_execution=setup_obj.sandbox.allow_shell_execution,
        allow_network_access=allow_net_access,
        allowed_builtin_tools=list(setup_obj.sandbox.allowed_builtin_tools),
        denied_builtin_tools=list(setup_obj.sandbox.denied_builtin_tools),
    )
    dto_ontology_policy: Literal["off", "recommend", "auto_accept"] = (
        "auto_accept"
        if setup_obj.ontology_policy.startswith("auto_accept")
        else ("off" if setup_obj.ontology_policy == "off" else "recommend")
    )
    harness_dto = HarnessConfigDTO(
        provider=setup_obj.provider,
        model_overrides=dict(setup_obj.model_overrides),
        agent_models=dict(setup_obj.agent_models),
        auth=auth_dto,
        budget=budget_dto,
        sandbox=sandbox_dto,
        steward_hold_policy=setup_obj.steward_hold_policy,
        ontology_policy=dto_ontology_policy,
        stage_output_policy=setup_obj.stage_output_policy,
        allow_cli_schema_omission_retry=allow_cli_retry,
    )
    return setup_obj, harness_dto


class PlatformConfigStateMixin:
    """Mixin managing platform & agent harness configuration, persistence, and provider probes."""

    _platform_config_initialized: bool = False
    _active_harness_setup: HarnessSetupConfig
    _active_harness_dto: HarnessConfigDTO
    _max_inline_bytes: int
    _cors_origins: list[str]
    _resolved_config_file_path: Path | None

    def _ensure_platform_config_loaded(self: BaseStateContainer | Any) -> None:
        """Lazily initialize platform & harness configuration from YAML and persisted JSON."""
        if getattr(self, "_platform_config_initialized", False):
            return

        settings = getattr(self, "settings", None)
        self._max_inline_bytes = (
            getattr(settings, "max_inline_bytes", DEFAULT_MAX_INLINE_BYTES)
            if settings is not None
            else DEFAULT_MAX_INLINE_BYTES
        )
        self._cors_origins = (
            list(getattr(settings, "cors_origins", _DEFAULT_CORS_ORIGINS))
            if settings is not None
            else list(_DEFAULT_CORS_ORIGINS)
        )

        # 1. Discover opendv.config.yaml if present
        cfg_file: Path | None = None
        if settings is not None and getattr(settings, "config_file_path", None) is not None:
            cand = Path(settings.config_file_path)
            if cand.is_file():
                cfg_file = cand.resolve()
        if cfg_file is None:
            repo_cand = self.repo_root / "opendv.config.yaml"
            if repo_cand.is_file():
                cfg_file = repo_cand.resolve()
        self._resolved_config_file_path = cfg_file

        raw_harness: dict[str, Any] = {}
        if cfg_file is not None:
            try:
                text = cfg_file.read_text(encoding="utf-8")
                parsed = (
                    json.loads(text)
                    if cfg_file.suffix.lower() == ".json"
                    else (yaml.safe_load(text) or {})
                )
                if isinstance(parsed, dict) and isinstance(parsed.get("harness"), dict):
                    raw_harness = _deep_merge_harness_dict(raw_harness, parsed["harness"])
            except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
                logging.getLogger(__name__).warning(
                    "Failed to load platform config file %s (%s); ignoring.",
                    cfg_file,
                    exc,
                )

        # 2. Merge persisted state_dir/platform_config.json if present
        persisted_path = self.state_dir / "platform_config.json"
        if persisted_path.is_file():
            try:
                persisted_data = json.loads(persisted_path.read_text(encoding="utf-8"))
                if isinstance(persisted_data, dict):
                    if isinstance(persisted_data.get("harness"), dict):
                        raw_harness = _deep_merge_harness_dict(
                            raw_harness, persisted_data["harness"]
                        )
                    if isinstance(persisted_data.get("max_inline_bytes"), int):
                        self._max_inline_bytes = persisted_data["max_inline_bytes"]
                        if settings is not None:
                            settings.max_inline_bytes = self._max_inline_bytes
                    if isinstance(persisted_data.get("cors_origins"), list):
                        self._cors_origins = [str(o) for o in persisted_data["cors_origins"]]
                        if settings is not None:
                            settings.cors_origins = list(self._cors_origins)
            except (OSError, json.JSONDecodeError) as exc:
                logging.getLogger(__name__).warning(
                    "Failed to load persisted platform_config.json %s (%s); ignoring.",
                    persisted_path,
                    exc,
                )

        try:
            setup_obj, harness_dto = _validate_and_normalize_harness(raw_harness)
        except (ValidationError, ValueError) as exc:
            logging.getLogger(__name__).warning(
                "Invalid persisted/file harness config (%s); falling back to defaults.",
                exc,
            )
            setup_obj, harness_dto = _validate_and_normalize_harness({})

        self._active_harness_setup = setup_obj
        self._active_harness_dto = harness_dto
        self._platform_config_initialized = True

    def get_active_harness_setup_dict(self: BaseStateContainer | Any) -> dict[str, Any]:
        """Return the active `HarnessSetupConfig` dictionary for pipeline execution."""
        self._ensure_platform_config_loaded()
        return self._active_harness_setup.model_dump(mode="json")

    def compute_resolved_persona_models(
        self: BaseStateContainer | Any,
        setup: HarnessSetupConfig | None = None,
    ) -> dict[str, str]:
        """Compute effective resolved model IDs for each canonical agent persona."""
        self._ensure_platform_config_loaded()
        active = setup or self._active_harness_setup
        resolved: dict[str, str] = {}
        for persona in _CANONICAL_PERSONAS:
            default_tier = resolve_default_tier_for_persona(persona)
            resolved[persona] = resolve_provider_model(
                active.provider,
                default_tier,
                active.model_overrides,
                persona=persona,
                agent_models=active.agent_models,
            )
        return resolved

    def probe_providers_readiness(
        self: BaseStateContainer | Any,
        active_harness: HarnessSetupConfig | None = None,
    ) -> list[ProviderReadinessDTO]:
        """Probe binary availability, Python SDK installation, and env var presence."""
        self._ensure_platform_config_loaded()
        setup = active_harness or self._active_harness_setup

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
                sdk_installed = _is_module_available("google.antigravity", "google.genai")
            elif provider == "claude":
                sdk_installed = _is_module_available("claude_agent_sdk", "anthropic")

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
            available = (
                binary_found if execution_mode == "cli" else (sdk_installed and env_key_present)
            )

            if setup.provider == provider and setup.auth.auth_mode != "none":
                auth_mode = setup.auth.auth_mode
            else:
                auth_mode = "cli_session" if execution_mode == "cli" else "api_key"

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

    def _is_web_ui_bundled(self: BaseStateContainer | Any) -> bool:
        """Check whether the compiled static Web UI bundle (`index.html`) is available."""
        env_static = os.environ.get("OPENDV_STATIC_DIR", "").strip()
        if env_static and (Path(env_static) / "index.html").is_file():
            return True
        repo_static = (
            self.repo_root
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

    def get_platform_config(self: BaseStateContainer | Any) -> dict[str, Any]:
        """Return serialized `PlatformConfigResponse` for the active workspace and harness."""
        self._ensure_platform_config_loaded()
        settings = getattr(self, "settings", None)
        server_host = (
            getattr(settings, "server_host", DEFAULT_SERVER_HOST)
            if settings
            else DEFAULT_SERVER_HOST
        )
        server_port = (
            getattr(settings, "server_port", DEFAULT_SERVER_PORT)
            if settings
            else DEFAULT_SERVER_PORT
        )

        resp = PlatformConfigResponse(
            workspace_root=str(self.workspace_root),
            state_dir=str(self.state_dir),
            cas_dir=str(self.cas_root),
            ledgers_dir=str(self.ledger_dir),
            server_host=server_host,
            server_port=server_port,
            max_inline_bytes=self._max_inline_bytes,
            cors_origins=list(self._cors_origins),
            config_file_path=(
                str(self._resolved_config_file_path) if self._resolved_config_file_path else None
            ),
            persisted_config_path=str(self.state_dir / "platform_config.json"),
            web_ui_bundled=self._is_web_ui_bundled(),
            harness=self._active_harness_dto,
            resolved_persona_models=self.compute_resolved_persona_models(
                self._active_harness_setup
            ),
            providers=self.probe_providers_readiness(self._active_harness_setup),
        )
        return resp.model_dump(mode="json")

    def validate_platform_config(
        self: BaseStateContainer | Any,
        req: PlatformConfigValidateRequest | dict[str, Any],
    ) -> dict[str, Any]:
        """Deterministically validate candidate Harness & Platform settings."""
        self._ensure_platform_config_loaded()
        if isinstance(req, PlatformConfigValidateRequest):
            raw_harness = req.harness
        elif isinstance(req, dict):
            raw_harness = req.get("harness", req) if "harness" in req else req
        else:
            raw_harness = {}

        try:
            setup_obj, harness_dto = _validate_and_normalize_harness(raw_harness)
        except ValidationError as exc:
            diagnostics: list[ConfigValidationDiagnosticDTO] = []
            for err in exc.errors():
                loc_parts = [str(p) for p in err.get("loc", ())]
                target = ".".join(loc_parts) if loc_parts else "harness"
                diagnostics.append(
                    ConfigValidationDiagnosticDTO(
                        code="HARNESS_CONFIG_INVALID",
                        severity="ERROR",
                        target=target,
                        message=str(err.get("msg", "Invalid harness configuration")),
                    )
                )
            val_resp = PlatformConfigValidationResponse(
                passed=False,
                diagnostics=diagnostics,
                harness=None,
                resolved_persona_models={},
                providers=self.probe_providers_readiness(self._active_harness_setup),
            )
            return val_resp.model_dump(mode="json")
        except ValueError as exc:
            val_resp = PlatformConfigValidationResponse(
                passed=False,
                diagnostics=[
                    ConfigValidationDiagnosticDTO(
                        code="HARNESS_CONFIG_INVALID",
                        severity="ERROR",
                        target="harness",
                        message=str(exc),
                    )
                ],
                harness=None,
                resolved_persona_models={},
                providers=self.probe_providers_readiness(self._active_harness_setup),
            )
            return val_resp.model_dump(mode="json")

        val_resp = PlatformConfigValidationResponse(
            passed=True,
            diagnostics=[],
            harness=harness_dto,
            resolved_persona_models=self.compute_resolved_persona_models(setup_obj),
            providers=self.probe_providers_readiness(setup_obj),
        )
        return val_resp.model_dump(mode="json")

    def update_platform_config(
        self: BaseStateContainer | Any,
        req: PlatformConfigUpdateRequest | dict[str, Any],
    ) -> dict[str, Any]:
        """Update active Platform & Agent Harness configuration and optionally persist to disk."""
        self._ensure_platform_config_loaded()
        try:
            req_obj = (
                req
                if isinstance(req, PlatformConfigUpdateRequest)
                else PlatformConfigUpdateRequest.model_validate(req)
            )
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

        if req_obj.harness is not None:
            current_dto_dict = self._active_harness_dto.model_dump(mode="json")
            merged_harness = _deep_merge_harness_dict(current_dto_dict, req_obj.harness)
            try:
                setup_obj, harness_dto = _validate_and_normalize_harness(merged_harness)
            except ValidationError as exc:
                raise ValueError(str(exc)) from exc
            self._active_harness_setup = setup_obj
            self._active_harness_dto = harness_dto

        settings = getattr(self, "settings", None)
        if req_obj.max_inline_bytes is not None:
            self._max_inline_bytes = req_obj.max_inline_bytes
            if settings is not None:
                settings.max_inline_bytes = req_obj.max_inline_bytes

        if req_obj.cors_origins is not None:
            self._cors_origins = list(req_obj.cors_origins)
            if settings is not None:
                settings.cors_origins = list(req_obj.cors_origins)

        if req_obj.persist:
            persisted_path = self.state_dir / "platform_config.json"
            persisted_payload = {
                "harness": self._active_harness_dto.model_dump(mode="json"),
                "max_inline_bytes": self._max_inline_bytes,
                "cors_origins": self._cors_origins,
            }
            persisted_path.parent.mkdir(parents=True, exist_ok=True)
            persisted_path.write_text(
                json.dumps(persisted_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        return self.get_platform_config()


__all__ = ["PlatformConfigStateMixin"]
