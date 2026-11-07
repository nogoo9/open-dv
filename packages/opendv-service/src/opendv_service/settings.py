"""Centralized runtime configuration for Open-DV services, CLI, and API (REQ-CFG-01)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ConfigurationError(ValueError):
    """Raised when required configuration is missing or invalid."""

    pass


DEFAULT_MAX_INLINE_BYTES: int = 50 * 1024 * 1024


class OpenDVSettings(BaseModel):
    """Authoritative settings model for OpenDV runtime environments."""

    workspace_root: Path | None = Field(
        default=None,
        description="Root directory for workspace file resolution and path containment.",
    )
    state_dir: Path | None = Field(
        default=None,
        description="Directory for persistent platform state (CAS, ledgers, runs).",
    )
    cas_dir: Path | None = Field(
        default=None,
        description="Root directory for Content Addressable Store blobs.",
    )
    ledgers_dir: Path | None = Field(
        default=None,
        description="Root directory for JSONL event ledgers.",
    )
    artifacts_dir: Path | None = Field(
        default=None,
        description="Root directory for exported artifacts.",
    )
    api_url: str | None = Field(
        default=None,
        description="Platform API URL for remote clients.",
    )
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8420",
            "http://127.0.0.1:8420",
        ],
        description="Allowed CORS origins for Platform API.",
    )
    server_host: str = Field(default="127.0.0.1", description="Default server bind host.")
    server_port: int = Field(default=8420, description="Default server bind port.")
    config_file_path: Path | None = Field(
        default=None,
        description="Path to active configuration file, if loaded.",
    )
    max_inline_bytes: int = Field(
        default=DEFAULT_MAX_INLINE_BYTES,
        description="Maximum allowed byte size for inline uploads or request payloads.",
    )

    @classmethod
    def load(
        cls,
        *,
        workspace_root: Path | None = None,
        state_dir: Path | None = None,
        storage_root: Path | None = None,
        config_file: Path | str | None = None,
        is_server: bool = False,
    ) -> OpenDVSettings:
        """Load settings observing strict precedence: explicit -> env -> config -> defaults."""
        checked_sources: list[str] = []

        # 1. Config file discovery
        cfg_path: Path | None = None
        if config_file is not None:
            cfg_path = Path(config_file).resolve()
            checked_sources.append(f"explicit config file ('{cfg_path}')")
        elif "OPENDV_CONFIG" in os.environ:
            cfg_path = Path(os.environ["OPENDV_CONFIG"]).resolve()
            checked_sources.append(f"env OPENDV_CONFIG ('{cfg_path}')")

        cfg_data: dict[str, Any] = {}
        if cfg_path is not None and cfg_path.is_file():
            text = cfg_path.read_text(encoding="utf-8")
            cfg_data = (
                json.loads(text)
                if cfg_path.suffix.lower() == ".json"
                else yaml.safe_load(text) or {}
            )

        # 2. Workspace root resolution
        resolved_ws: Path | None = None
        if workspace_root is not None:
            resolved_ws = Path(workspace_root).resolve()
            checked_sources.append(f"explicit workspace_root argument ('{resolved_ws}')")
        elif "OPENDV_WORKSPACE_ROOT" in os.environ and os.environ["OPENDV_WORKSPACE_ROOT"].strip():
            resolved_ws = Path(os.environ["OPENDV_WORKSPACE_ROOT"].strip()).resolve()
            checked_sources.append(f"env OPENDV_WORKSPACE_ROOT ('{resolved_ws}')")
        elif "OPENDV_WORKSPACE_ROOT" in os.environ:
            checked_sources.append("env OPENDV_WORKSPACE_ROOT (empty)")
        elif cfg_data.get("workspace_root") is not None and str(cfg_data["workspace_root"]).strip():
            resolved_ws = Path(str(cfg_data["workspace_root"]).strip()).resolve()
            checked_sources.append(f"config file key 'workspace_root' ('{resolved_ws}')")
        elif "workspace_root" in cfg_data:
            checked_sources.append("config file key 'workspace_root' (null/empty)")
        elif not is_server:
            # Standalone CLI mode defaults to cwd
            resolved_ws = Path.cwd().resolve()
            checked_sources.append(f"standalone CLI fallback (Path.cwd(): '{resolved_ws}')")

        if is_server and resolved_ws is None:
            sources_str = ", ".join(checked_sources) or "none checked"
            raise ConfigurationError(
                f"Missing required configuration 'workspace_root' for Platform API server. "
                f"Checked sources: {sources_str}. Please provide --workspace, "
                f"set OPENDV_WORKSPACE_ROOT, or configure 'workspace_root' in opendv.config.yaml."
            )

        # 3. State dir / storage root resolution
        resolved_state: Path | None = None
        effective_storage = storage_root or state_dir
        if effective_storage is not None:
            resolved_state = Path(effective_storage).resolve()
        elif "OPENDV_STORAGE_ROOT" in os.environ:
            resolved_state = Path(os.environ["OPENDV_STORAGE_ROOT"]).resolve()
        elif "OPENDV_STATE_DIR" in os.environ:
            resolved_state = Path(os.environ["OPENDV_STATE_DIR"]).resolve()
        elif cfg_data.get("storage_root") is not None:
            resolved_state = Path(cfg_data["storage_root"]).resolve()
        elif cfg_data.get("state_dir") is not None:
            resolved_state = Path(cfg_data["state_dir"]).resolve()
        elif resolved_ws is not None:
            resolved_state = (resolved_ws / ".opendv" / "state").resolve()

        # 4. Storage subdirectories
        resolved_cas = (resolved_state / "cas").resolve() if resolved_state else None
        resolved_ledgers = (resolved_state / "ledgers").resolve() if resolved_state else None
        resolved_artifacts = (resolved_state / "artifacts").resolve() if resolved_state else None

        # 5. CORS origins
        cors_val = cfg_data.get("cors_origins")
        if "OPENDV_CORS_ORIGINS" in os.environ:
            cors_env = os.environ["OPENDV_CORS_ORIGINS"]
            cors_val = [o.strip() for o in cors_env.split(",") if o.strip()]

        init_kwargs: dict[str, Any] = {
            "workspace_root": resolved_ws,
            "state_dir": resolved_state,
            "cas_dir": resolved_cas,
            "ledgers_dir": resolved_ledgers,
            "artifacts_dir": resolved_artifacts,
            "api_url": os.environ.get("OPENDV_API_URL", cfg_data.get("api_url")),
            "config_file_path": cfg_path,
        }
        if cors_val is not None:
            init_kwargs["cors_origins"] = cors_val

        return cls(**init_kwargs)

    def to_redacted_dict(self) -> dict[str, Any]:
        """Return safe, secrets-redacted settings dictionary for health probe."""
        return {
            "workspace_root": str(self.workspace_root) if self.workspace_root else None,
            "state_dir": str(self.state_dir) if self.state_dir else None,
            "cas_dir": str(self.cas_dir) if self.cas_dir else None,
            "ledgers_dir": str(self.ledgers_dir) if self.ledgers_dir else None,
            "artifacts_dir": str(self.artifacts_dir) if self.artifacts_dir else None,
            "api_url": self.api_url,
            "cors_origins": self.cors_origins,
            "config_file_path": str(self.config_file_path) if self.config_file_path else None,
        }


__all__ = [
    "ConfigurationError",
    "OpenDVSettings",
]
