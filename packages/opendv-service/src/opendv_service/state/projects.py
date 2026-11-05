"""Project registration and bronze source tracking for opendv-service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import yaml

from opendv_service.legacy_demo import PE_DEMO_CONTEXT_REFS
from opendv_service.models import (
    ArtifactMetadataResponse,
    CreateProjectRequest,
    ProjectResponse,
)
from opendv_service.state.cas import store_cas_artifact

if TYPE_CHECKING:
    from opendv_service.state.base import BaseStateContainer


class ProjectStateMixin:
    """Data Vault 2.0 project registration mixin."""

    def create_project(
        self: BaseStateContainer,
        req: CreateProjectRequest,
    ) -> ProjectResponse:
        """Register or update a Data Vault 2.0 project."""
        for rel_path in [
            req.contract_path,
            req.canonical_ir_path,
            req.graph_a_ontology_path,
            req.graph_b_path,
            *req.bronze_sources,
        ]:
            if rel_path is not None:
                self._resolve_repo_path(rel_path)

        graph_a_sha = PE_DEMO_CONTEXT_REFS["graph_a_sha256"]
        if req.graph_a_ontology_path is not None:
            resolved_ont = self._resolve_repo_path(req.graph_a_ontology_path)
            if resolved_ont.is_file():
                raw_bytes = resolved_ont.read_bytes()
                graph_a_sha = store_cas_artifact(self.cas_root, raw_bytes).upper()
                try:
                    parsed_ont: Any = yaml.safe_load(raw_bytes.decode("utf-8"))
                except Exception:
                    parsed_ont = raw_bytes.decode("utf-8", errors="replace")
                art_meta = ArtifactMetadataResponse(
                    sha256=graph_a_sha,
                    byte_length=len(raw_bytes),
                    media_type="application/yaml",
                    artifact_name=resolved_ont.name,
                    content=parsed_ont,
                )
                self.artifacts[graph_a_sha] = art_meta
                self.artifacts[graph_a_sha.lower()] = art_meta

        self.project_bronze_sources[req.project_id] = list(req.bronze_sources)
        proj = ProjectResponse(
            project_id=req.project_id,
            display_name=req.display_name,
            domain_namespace=req.domain_namespace,
            graph_a_sha256=graph_a_sha,
            latest_run_id=None,
            created_at_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            contract_path=req.contract_path,
            canonical_ir_path=req.canonical_ir_path,
            graph_a_ontology_path=req.graph_a_ontology_path,
            graph_b_path=req.graph_b_path,
        )
        self.projects[req.project_id] = proj
        return proj
