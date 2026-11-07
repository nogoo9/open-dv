"""PlatformStateContainer unifying domain state mixins."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from opendv_service.seams import PipelineRunner
from opendv_service.state.base import BaseStateContainer
from opendv_service.state.cas import ArtifactStateMixin, store_cas_artifact
from opendv_service.state.holds import HoldStateMixin
from opendv_service.state.pipeline import PipelineStateMixin
from opendv_service.state.pipeline_execution import PipelineExecutionMixin
from opendv_service.state.projects import ProjectStateMixin
from opendv_service.state.runs import RunStateMixin


class PlatformStateContainer(
    ProjectStateMixin,
    RunStateMixin,
    HoldStateMixin,
    PipelineStateMixin,
    PipelineExecutionMixin,
    ArtifactStateMixin,
    BaseStateContainer,
):
    """In-memory and CAS/JSONL-backed platform state container for `/api/v1` routers."""

    def __init__(
        self,
        workspace_root: Path | None = None,
        storage_root: Path | None = None,
        *,
        repo_root: Path | None = None,
        state_dir: Path | None = None,
        pipeline_runner: PipelineRunner | None = None,
        settings: Any | None = None,
    ) -> None:
        super().__init__(
            workspace_root=workspace_root,
            storage_root=storage_root,
            repo_root=repo_root,
            state_dir=state_dir,
            settings=settings,
        )
        self._custom_pipeline_runner = pipeline_runner


# Domain aliases
StateStore = PlatformStateContainer

__all__ = [
    "BaseStateContainer",
    "PlatformStateContainer",
    "StateStore",
    "store_cas_artifact",
]
