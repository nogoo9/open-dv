"""Private Equity demo seed state initialization (retained until P3.2 / P5.2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from opendv_service.legacy_demo import PE_DEMO_CONTEXT_REFS
from opendv_service.models import (
    ArtifactMetadataResponse,
    ProjectResponse,
)
from opendv_service.state.cas import store_cas_artifact

if TYPE_CHECKING:
    from opendv_service.state.base import BaseStateContainer
    from opendv_service.state.runs import RunStateMixin


class DemoStateMixin:
    """Legacy demo project and run seeding mixin."""

    def _seed_canonical_pe_demo(self: BaseStateContainer | RunStateMixin) -> None:
        """Seed the canonical Apex Horizon PE Buyout Vault v5.0 project, run, and events."""
        project_id = "proj-apex-horizon-pe"
        run_id = "run-pe-apex-horizon-20261030"
        self.projects[project_id] = ProjectResponse(
            project_id=project_id,
            display_name="Apex Horizon Capital Partners — Private Equity Vault",
            domain_namespace="pe_asset_management",
            graph_a_sha256=PE_DEMO_CONTEXT_REFS["graph_a_sha256"],
            latest_run_id=run_id,
            created_at_utc="2026-10-30T08:55:00Z",
            contract_path="examples/private_equity/dv_pe_model_contract.yaml",
            canonical_ir_path="examples/private_equity/pe_canonical_ir.json",
            graph_a_ontology_path="examples/private_equity/pe_graph_a_ontology.yaml",
            graph_b_path="examples/private_equity/pe_graph_b_profile.json",
        )

        seed_files: list[tuple[str, str, Path]] = [
            (
                "graph_a_sha256",
                "application/yaml",
                self.repo_root / "examples" / "private_equity" / "pe_graph_a_ontology.yaml",
            ),
            (
                "graph_b_sha256",
                "application/json",
                self.repo_root / "examples" / "private_equity" / "pe_graph_b_profile.json",
            ),
            (
                "contract_yaml_pre_sha256",
                "application/yaml",
                self.repo_root / "examples" / "private_equity" / "dv_pe_model_contract.yaml",
            ),
            (
                "contract_yaml_post_sha256",
                "application/yaml",
                self.repo_root / "examples" / "private_equity" / "dv_pe_model_contract.yaml",
            ),
            (
                "canonical_ir_sha256",
                "application/json",
                self.repo_root / "examples" / "private_equity" / "pe_canonical_ir.json",
            ),
        ]

        for ref_key, media_type, file_path in seed_files:
            if not file_path.is_file():
                continue
            raw_text = file_path.read_text(encoding="utf-8")
            raw_bytes = raw_text.encode("utf-8")
            actual_sha = store_cas_artifact(self.cas_root, raw_bytes)
            canonical_sha = PE_DEMO_CONTEXT_REFS[ref_key].upper()
            parsed_content: Any
            if media_type == "application/json":
                parsed_content = json.loads(raw_text)
            else:
                parsed_content = yaml.safe_load(raw_text)

            for sha_key in {actual_sha, actual_sha.lower(), canonical_sha, canonical_sha.lower()}:
                self.artifacts[sha_key] = ArtifactMetadataResponse(
                    sha256=sha_key.upper(),
                    byte_length=len(raw_bytes),
                    media_type=media_type,
                    artifact_name=file_path.name,
                    content=parsed_content,
                )

        self._init_run_artifacts(
            run_id=run_id,
            project_id=project_id,
            harness_provider="antigravity",
            started_at_utc="2026-10-30T09:00:00Z",
        )
