"""Project registration and bronze source tracking for opendv-service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import yaml

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
            getattr(req, "contract_path", None),
            getattr(req, "canonical_ir_path", None),
            getattr(req, "graph_a_ontology_path", None),
            getattr(req, "graph_b_path", None),
            *getattr(req, "bronze_sources", []),
        ]:
            if rel_path is not None:
                self._resolve_repo_path(rel_path)

        graph_a_sha: str | None = None
        ont_path = getattr(req, "graph_a_ontology_path", None)
        if ont_path is not None:
            resolved_ont = self._resolve_repo_path(ont_path)
            if resolved_ont.is_file():
                raw_bytes = resolved_ont.read_bytes()
                graph_a_sha = store_cas_artifact(self.cas_root, raw_bytes).upper()
                try:
                    parsed_ont: Any = yaml.safe_load(raw_bytes.decode("utf-8"))
                except (yaml.YAMLError, UnicodeDecodeError):
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

        self.project_bronze_sources[req.project_id] = list(getattr(req, "bronze_sources", []))
        eff_display_name = getattr(req, "display_name", None) or getattr(
            req, "name", "Open-DV Project"
        )
        proj = ProjectResponse(
            project_id=req.project_id,
            display_name=eff_display_name,
            domain_namespace=getattr(req, "domain_namespace", None) or "general",
            graph_a_sha256=graph_a_sha,
            latest_run_id=None,
            created_at_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            contract_path=getattr(req, "contract_path", None),
            canonical_ir_path=getattr(req, "canonical_ir_path", None),
            graph_a_ontology_path=getattr(req, "graph_a_ontology_path", None),
            graph_b_path=getattr(req, "graph_b_path", None),
        )
        self.projects[req.project_id] = proj
        return proj
