"""Platform configuration and serving mixin for LocalExecutor."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from opendv_cli_common import ExecutionResult
from opendv_contracts import (
    PlatformConfigResponse,
    PlatformConfigUpdateRequest,
    PlatformConfigValidateRequest,
    PlatformConfigValidationResponse,
)
from opendv_contracts.dtos.platform_config import (
    HarnessConfigDTO,
)
from opendv_contracts.errors import OpenDVError
from opendv_service.settings import (
    DEFAULT_CORS_ORIGINS,
    DEFAULT_SERVER_HOST,
    DEFAULT_SERVER_PORT,
    is_loopback_hostname,
)
from opendv_service.studio.workspace_security import mint_launch_nonce


def _load_platform_server_main() -> Any | None:
    """Resolve the registered `opendv-platform` console_scripts entrypoint."""
    from importlib.metadata import entry_points

    for ep in entry_points(group="console_scripts"):
        if ep.name == "opendv-platform":
            try:
                return ep.load()
            except ImportError:
                return None
    return None


class PlatformExecutionMixin:
    """Mixin providing platform serve and config commands for LocalExecutor."""

    def _fallback_platform_config_response(
        self,
        *,
        harness_updates: dict[str, Any] | None = None,
        persist: bool = False,
    ) -> dict[str, Any]:
        settings = getattr(self._service, "settings", None)
        state_dir = Path(
            getattr(self._service, "storage_root", None)
            or (getattr(settings, "state_dir", None) if settings else None)
            or (self.workspace_root / ".opendv" / "state")
        ).resolve()
        persisted_path = state_dir / "platform_config.json"

        base_harness: dict[str, Any] = {}
        if persisted_path.is_file():
            try:
                loaded = json.loads(persisted_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    base_harness = dict(loaded.get("harness", loaded))
            except (ValueError, TypeError, KeyError, OSError):
                base_harness = {}

        if harness_updates:
            for k, v in harness_updates.items():
                if isinstance(v, dict) and isinstance(base_harness.get(k), dict):
                    base_harness[k] = {**base_harness[k], **v}
                else:
                    base_harness[k] = v

        harness_dto = HarnessConfigDTO.model_validate(base_harness)
        if persist:
            persisted_path.parent.mkdir(parents=True, exist_ok=True)
            persisted_path.write_text(
                json.dumps({"harness": harness_dto.model_dump(mode="json")}, indent=2),
                encoding="utf-8",
            )

        reasoning = harness_dto.model_overrides.get("reasoning", "gemini-4-argon-high")
        fast = harness_dto.model_overrides.get("fast", "gemini-3.8-flash-high")
        resolved_personas = {
            "vault-first-mate": harness_dto.agent_models.get("vault-first-mate", reasoning),
            "scout-source": harness_dto.agent_models.get("scout-source", fast),
            "scout-unstructured": harness_dto.agent_models.get("scout-unstructured", fast),
            "ship-vault": harness_dto.agent_models.get("ship-vault", reasoning),
        }

        resp = PlatformConfigResponse(
            workspace_root=str(self.workspace_root),
            state_dir=str(state_dir),
            cas_dir=str(state_dir / "cas"),
            ledgers_dir=str(state_dir / "ledgers"),
            server_host=(
                getattr(settings, "server_host", DEFAULT_SERVER_HOST)
                if settings
                else DEFAULT_SERVER_HOST
            ),
            server_port=(
                getattr(settings, "server_port", DEFAULT_SERVER_PORT)
                if settings
                else DEFAULT_SERVER_PORT
            ),
            max_inline_bytes=(
                getattr(settings, "max_inline_bytes", 10 * 1024 * 1024)
                if settings
                else 10 * 1024 * 1024
            ),
            cors_origins=(
                list(getattr(settings, "cors_origins", []))
                if settings
                else list(DEFAULT_CORS_ORIGINS)
            ),
            config_file_path=(
                str(settings.config_file_path)
                if settings and getattr(settings, "config_file_path", None)
                else None
            ),
            persisted_config_path=str(persisted_path),
            web_ui_bundled=False,
            harness=harness_dto,
            resolved_persona_models=resolved_personas,
            providers=[],
        )
        return resp.model_dump(mode="json")

    def _fallback_validate_platform_config(
        self,
        harness_candidate: dict[str, Any],
    ) -> dict[str, Any]:
        provider = str(harness_candidate.get("provider") or "agy")
        auth = harness_candidate.get("auth") or {}
        auth_mode = str(auth.get("auth_mode") or auth.get("mode") or "none")

        if provider in ("agy", "agy-cli") and auth_mode == "aws_bedrock":
            return PlatformConfigValidationResponse(
                passed=False,
                diagnostics=[
                    {
                        "code": "HARNESS_AUTH_INCOMPATIBLE",
                        "severity": "ERROR",
                        "target": "auth.auth_mode",
                        "message": f"Provider '{provider}' does not support aws_bedrock.",
                    }
                ],
                harness=None,
                resolved_persona_models={},
                providers=[],
            ).model_dump(mode="json")

        try:
            dto = HarnessConfigDTO.model_validate(harness_candidate)
            reasoning = dto.model_overrides.get("reasoning", "gemini-4-argon-high")
            fast = dto.model_overrides.get("fast", "gemini-3.8-flash-high")
            return PlatformConfigValidationResponse(
                passed=True,
                diagnostics=[],
                harness=dto,
                resolved_persona_models={
                    "vault-first-mate": dto.agent_models.get("vault-first-mate", reasoning),
                    "scout-source": dto.agent_models.get("scout-source", fast),
                    "scout-unstructured": dto.agent_models.get("scout-unstructured", fast),
                    "ship-vault": dto.agent_models.get("ship-vault", reasoning),
                },
                providers=[],
            ).model_dump(mode="json")
        except (ValueError, TypeError, KeyError) as exc:
            return PlatformConfigValidationResponse(
                passed=False,
                diagnostics=[
                    {
                        "code": "HARNESS_CONFIG_INVALID",
                        "severity": "ERROR",
                        "target": "harness",
                        "message": str(exc),
                    }
                ],
                harness=None,
                resolved_persona_models={},
                providers=[],
            ).model_dump(mode="json")

    def _handle_config(self, args: argparse.Namespace) -> ExecutionResult:
        sub = (
            getattr(args, "config_action", None)
            or getattr(args, "config_subcommand", None)
            or getattr(args, "subcommand_action", None)
            or "show"
        ).lower()

        if sub == "show":
            if hasattr(self._service, "get_platform_config"):
                res = self._service.get_platform_config()
                payload = res.model_dump(mode="json") if hasattr(res, "model_dump") else dict(res)
            else:
                payload = self._fallback_platform_config_response()
            payload["mode"] = "local"
            return 0, payload

        if sub == "set":
            harness_updates = self._build_harness_dict_from_args(args)
            req = PlatformConfigUpdateRequest(harness=harness_updates, persist=True)
            if hasattr(self._service, "update_platform_config"):
                res = self._service.update_platform_config(req)
                payload = res.model_dump(mode="json") if hasattr(res, "model_dump") else dict(res)
            else:
                payload = self._fallback_platform_config_response(
                    harness_updates=harness_updates,
                    persist=True,
                )
            payload["mode"] = "local"
            return 0, payload

        if sub == "validate":
            harness_candidate = self._build_harness_dict_from_args(args)
            req = PlatformConfigValidateRequest(harness=harness_candidate)
            if hasattr(self._service, "validate_platform_config"):
                res = self._service.validate_platform_config(req)
                payload = res.model_dump(mode="json") if hasattr(res, "model_dump") else dict(res)
            else:
                payload = self._fallback_validate_platform_config(harness_candidate)
            payload["mode"] = "local"
            passed = bool(payload.get("passed", False))
            return (0 if passed else 1), payload

        return 2, {
            "status": "ERROR",
            "error": f"Unknown config subcommand: '{sub}'",
        }

    _execute_config = _handle_config

    # 10. serve
    def _handle_serve(self, args: argparse.Namespace) -> ExecutionResult:
        """Launch the unified Platform Server & Web Explorer UI.

        Temporarily sets OPENDV_CONFIG and OPENDV_WORKSPACE_ROOT in os.environ
        for the server lifetime and restores the previous environment on exit.
        """
        import logging

        host = str(getattr(args, "host", None) or DEFAULT_SERVER_HOST)
        port = int(getattr(args, "port", None) or DEFAULT_SERVER_PORT)
        config_path = getattr(args, "config", None)
        workspace_path = getattr(args, "workspace", None)
        open_browser = bool(getattr(args, "open_browser", False) or getattr(args, "open", False))

        prev_config = os.environ.get("OPENDV_CONFIG")
        prev_workspace = os.environ.get("OPENDV_WORKSPACE_ROOT")
        try:
            if config_path:
                os.environ["OPENDV_CONFIG"] = str(Path(config_path).resolve())
            if workspace_path:
                os.environ["OPENDV_WORKSPACE_ROOT"] = str(Path(workspace_path).resolve())
            elif "OPENDV_WORKSPACE_ROOT" not in os.environ:
                os.environ["OPENDV_WORKSPACE_ROOT"] = str(self.workspace_root)

            server_mode = (
                (
                    os.environ.get("OPENDV_SERVER_MODE")
                    or os.environ.get("OPENDV_DEPLOYMENT_MODE")
                    or "local_standalone"
                )
                .strip()
                .lower()
            )
            is_loopback = is_loopback_hostname(host)
            studio_launch_url: str | None = None
            if server_mode == "local_standalone" and is_loopback:
                from opendv_cli.commands.ontology_studio import (
                    _err_from_opendv_error,
                    resolve_studio_principal,
                )

                auth_res = resolve_studio_principal(args)
                if isinstance(auth_res[0], int):
                    return auth_res  # type: ignore[return-value]
                principal_id, studio_role = auth_res  # type: ignore[misc]
                exec_mod = sys.modules.get("opendv_cli.executor")
                mint_fn = getattr(exec_mod, "mint_launch_nonce", mint_launch_nonce)
                try:
                    nonce = mint_fn(principal=principal_id, role=studio_role, ttl=60.0)
                except OpenDVError as exc:
                    return _err_from_opendv_error(exc)
                display_host = "[::1]" if host.strip() == "::1" else host.strip()
                studio_launch_url = f"http://{display_host}:{port}/ontology#token={nonce}"
                wants_json = bool(
                    getattr(args, "json", False) or getattr(args, "format", None) == "json"
                )
                out_stream = sys.stderr if wants_json else sys.stdout
                print(f"Ontology Studio Launch URL: {studio_launch_url}", file=out_stream)

            cli_argv = ["--host", host, "--port", str(port)]
            if config_path:
                cli_argv.extend(["--config", str(config_path)])
            if workspace_path:
                cli_argv.extend(["--workspace", str(workspace_path)])
            if open_browser:
                cli_argv.append("--open")

            exec_mod = sys.modules.get("opendv_cli.executor")
            load_fn = getattr(exec_mod, "_load_platform_server_main", _load_platform_server_main)
            launcher_main = load_fn()
            if callable(launcher_main):
                rc = launcher_main(cli_argv)
                exit_code = int(rc) if isinstance(rc, int) else 0
                res_payload: dict[str, Any] = {
                    "status": "OK" if exit_code == 0 else "ERROR",
                    "mode": "local",
                    "host": host,
                    "port": port,
                    "url": f"http://{host}:{port}",
                }
                if studio_launch_url:
                    res_payload["studio_launch_url"] = studio_launch_url
                return exit_code, res_payload

            logging.getLogger(__name__).debug(
                "opendv-platform entrypoint not found; falling back to uvicorn.run"
            )

            timer = None
            if open_browser:
                import threading
                import webbrowser

                target_open_url = studio_launch_url or f"http://{host}:{port}"
                timer = threading.Timer(0.5, webbrowser.open, args=[target_open_url])
                timer.daemon = True
                timer.start()

            import uvicorn

            try:
                uvicorn.run("opendv_platform_api.main:app", host=host, port=port)
            finally:
                if timer is not None:
                    timer.cancel()
            res_payload = {
                "status": "OK",
                "mode": "local",
                "host": host,
                "port": port,
                "url": f"http://{host}:{port}",
            }
            if studio_launch_url:
                res_payload["studio_launch_url"] = studio_launch_url
            return 0, res_payload
        finally:
            if prev_config is None:
                os.environ.pop("OPENDV_CONFIG", None)
            else:
                os.environ["OPENDV_CONFIG"] = prev_config
            if prev_workspace is None:
                os.environ.pop("OPENDV_WORKSPACE_ROOT", None)
            else:
                os.environ["OPENDV_WORKSPACE_ROOT"] = prev_workspace

    _execute_serve = _handle_serve


__all__ = ["PlatformExecutionMixin", "_load_platform_server_main"]
