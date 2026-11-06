"""Parsing and loading utilities for ontology deltas, base Graph A, and deployed contracts.

Decision D1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from opendv_contracts.dtos.ontology import OntologyDelta
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.paths import resolve_workspace_path
from opendv_service.errors import InputValidationError
from opendv_sources import load_graph_a_source


def load_ontology_delta(
    delta_or_path_or_content: Any,
    *,
    workspace_root: Path | None = None,
) -> OntologyDelta:
    """Load, parse, and validate an OntologyDelta from a model, dict, path, or inline text."""
    if isinstance(delta_or_path_or_content, OntologyDelta):
        return delta_or_path_or_content

    if isinstance(delta_or_path_or_content, dict):
        d = delta_or_path_or_content
        if "delta" in d and ("proposal_id" in d or "recommender_id" in d):
            d = d["delta"]
        try:
            return OntologyDelta.model_validate(d)
        except (ValueError, TypeError) as exc:
            raise InputValidationError(f"Invalid OntologyDelta dictionary: {exc}") from exc

    if isinstance(delta_or_path_or_content, (str, Path)):
        raw_str = str(delta_or_path_or_content).strip()

        # Check if raw_str is a file path
        candidate_path: Path | None = None
        if "\n" not in raw_str and not raw_str.startswith(("{", "[")):
            try:
                candidate_path = (
                    resolve_workspace_path(raw_str, workspace_root=workspace_root)
                    if workspace_root
                    else Path(raw_str)
                )
            except (ValueError, RuntimeError, OSError):
                candidate_path = Path(raw_str)

        if candidate_path and candidate_path.is_file():
            content = candidate_path.read_text(encoding="utf-8")
        else:
            content = raw_str

        # Parse YAML or JSON
        try:
            parsed = yaml.safe_load(content)
        except (yaml.YAMLError, ValueError, TypeError) as exc:
            raise InputValidationError(f"Failed to parse ontology delta YAML/JSON: {exc}") from exc

        if not isinstance(parsed, dict):
            raise InputValidationError(
                f"Expected dictionary root for OntologyDelta, got {type(parsed).__name__}"
            )

        if "delta" in parsed and ("proposal_id" in parsed or "recommender_id" in parsed):
            parsed = parsed["delta"]

        try:
            return OntologyDelta.model_validate(parsed)
        except (ValueError, TypeError) as exc:
            raise InputValidationError(f"Invalid OntologyDelta payload: {exc}") from exc

    raise InputValidationError(
        f"Unsupported delta representation type: {type(delta_or_path_or_content).__name__}"
    )


def load_base_ontology(
    base_or_path_or_content: Any,
    *,
    workspace_root: Path | None = None,
) -> GraphAOntology:
    """Load, hash, and validate a GraphAOntology via opendv_sources unified loader."""
    if isinstance(base_or_path_or_content, GraphAOntology):
        return base_or_path_or_content

    loaded = load_graph_a_source(
        base_or_path_or_content,
        workspace_root=workspace_root,
        allow_temp=True,
    )
    return loaded.ontology


def load_deployed_contract(
    contract_or_path_or_content: Any,
    *,
    workspace_root: Path | None = None,
) -> Any | None:
    """Load deployed Data Vault contract from object, path, or inline text."""
    if contract_or_path_or_content is None:
        return None

    if hasattr(contract_or_path_or_content, "model_dump"):
        return contract_or_path_or_content

    if isinstance(contract_or_path_or_content, dict):
        return contract_or_path_or_content

    if isinstance(contract_or_path_or_content, (str, Path)):
        raw_str = str(contract_or_path_or_content).strip()
        candidate_path: Path | None = None
        if "\n" not in raw_str and not raw_str.startswith(("{", "[")):
            try:
                candidate_path = (
                    resolve_workspace_path(raw_str, workspace_root=workspace_root)
                    if workspace_root
                    else Path(raw_str)
                )
            except (ValueError, RuntimeError, OSError):
                candidate_path = Path(raw_str)

        if candidate_path and candidate_path.is_file():
            content = candidate_path.read_text(encoding="utf-8")
        else:
            content = raw_str

        try:
            return yaml.safe_load(content)
        except (yaml.YAMLError, ValueError, TypeError) as exc:
            raise InputValidationError(
                f"Failed to parse deployed contract YAML/JSON: {exc}"
            ) from exc

    return None


__all__ = [
    "load_base_ontology",
    "load_deployed_contract",
    "load_ontology_delta",
]
