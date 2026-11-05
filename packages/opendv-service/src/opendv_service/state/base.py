"""Base state container protocol, common fields, and path utilities."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from opendv_agent_harness import StewardHoldManager
from opendv_core import (
    SchemaRegistry,
    get_default_schema_registry,
)
from opendv_core.paths import PathContainmentError, resolve_workspace_path
from opendv_service.models import (
    ArtifactMetadataResponse,
    CaptainHoldDecisionResponse,
    ProjectResponse,
    ProjectRunDTO,
    SandboxExecutionResponse,
)
from opendv_telemetry import (
    ContentAddressableStore,
    VaultLedgerEmitter,
)

# Canonical workspace path containment resolver alias (DUP-04 / T13)
resolve_safe_repo_path = resolve_workspace_path


def _load_structured_mapping(path: Path, label: str) -> dict[str, Any]:
    """Load a YAML or JSON mapping from `path`, raising `ValueError` on syntax or type errors."""
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Failed to parse {label} file '{path}': {exc}") from exc


class BaseStateContainer:
    """Core in-memory and on-disk state storage fields for PlatformStateContainer."""

    def __init__(
        self,
        workspace_root: Path | None = None,
        storage_root: Path | None = None,
        *,
        repo_root: Path | None = None,
        state_dir: Path | None = None,
    ) -> None:
        eff_ws = workspace_root if workspace_root is not None else repo_root
        self._explicit_workspace = eff_ws is not None
        if eff_ws is None:
            env_ws = os.environ.get("OPENDV_WORKSPACE_ROOT", "").strip()
            self._workspace_root = Path(env_ws or Path.cwd()).resolve()
        else:
            self._workspace_root = Path(eff_ws).resolve()
        self.workspace_root = self._workspace_root
        self.repo_root = self._workspace_root

        eff_storage = storage_root if storage_root is not None else state_dir
        self._explicit_storage = eff_storage is not None
        if eff_storage is None:
            self.storage_root = (self._workspace_root / ".opendv" / "state").resolve()
        else:
            self.storage_root = Path(eff_storage).resolve()

        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.state_dir = self.storage_root

        self.cas_root = self.state_dir / "cas"
        self.cas_root.mkdir(parents=True, exist_ok=True)
        self.cas = ContentAddressableStore(self.cas_root)
        self.cas_store = self.cas
        self.ledger_dir = self.state_dir / "ledgers"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)

        self.schema_registry: SchemaRegistry = get_default_schema_registry()

        self.projects: dict[str, ProjectResponse] = {}
        self.project_bronze_sources: dict[str, list[str]] = {}
        self.runs: dict[str, ProjectRunDTO] = {}
        self.run_events: dict[str, list[dict[str, Any]]] = {}
        self.run_workbench_events: dict[str, list[dict[str, Any]]] = {}
        self.run_events_by_view: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self.run_ledgers: dict[str, VaultLedgerEmitter] = {}
        self.hold_managers: dict[str, StewardHoldManager] = {}
        self.run_gate_reports: dict[str, dict[str, Any]] = {}
        self.gate_reports: dict[str, dict[str, Any]] = self.run_gate_reports
        self.run_sandbox_history: dict[str, list[SandboxExecutionResponse]] = {}
        self.run_decisions: dict[str, list[CaptainHoldDecisionResponse]] = {}
        self.artifacts: dict[str, ArtifactMetadataResponse] = {}
        self.pipeline_reports: dict[str, dict[str, Any]] = {}
        self._pipeline_run_seq: int = 0

    def _resolve_repo_path(self, rel_path: str | Path) -> Path:
        """Resolve a repository-relative path and enforce repository-root containment."""
        try:
            return resolve_workspace_path(
                str(rel_path),
                workspace_root=self.repo_root,
                allow_temp=True,
            )
        except PathContainmentError as exc:
            raise ValueError(str(exc)) from exc
