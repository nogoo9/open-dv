"""Base state container protocol, common fields, and path utilities."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import yaml

from opendv_agent_harness import StewardHoldManager
from opendv_core import (
    SchemaRegistry,
    find_workspace_root,
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

REPO_ROOT = find_workspace_root()

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
        repo_root: Path | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self.repo_root = (repo_root or REPO_ROOT).resolve()
        self._tmp_dir: tempfile.TemporaryDirectory[str] | None
        if state_dir is None:
            self._tmp_dir = tempfile.TemporaryDirectory(prefix="opendv_platform_state_")
            self.state_dir = Path(self._tmp_dir.name)
        else:
            self._tmp_dir = None
            self.state_dir = state_dir.resolve()
            self.state_dir.mkdir(parents=True, exist_ok=True)

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
