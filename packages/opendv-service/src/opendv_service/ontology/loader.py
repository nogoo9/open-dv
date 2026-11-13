"""Parsing and loading utilities for ontology deltas, base Graph A, and deployed contracts.

Decision D1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from opendv_contracts.dtos.ontology import OntologyDelta
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.paths import PathContainmentError, resolve_workspace_path
from opendv_service.errors import InputValidationError
from opendv_sources import load_graph_a_source


def _resolve_candidate_file_path(
    raw_str: str,
    *,
    workspace_root: Path | None = None,
) -> Path | None:
    """Safely resolve candidate file path with strict workspace containment enforcement."""
    has_newlines = "\n" in raw_str
    starts_with_json = raw_str.startswith(("{", "["))
    if has_newlines or starts_with_json:
        return None

    is_path_like = (
        "/" in raw_str
        or "\\" in raw_str
        or raw_str.endswith((".yaml", ".yml", ".json"))
        or raw_str.startswith((".", "~"))
    )

    if workspace_root is not None:
        try:
            resolved = resolve_workspace_path(raw_str, workspace_root=workspace_root)
            if resolved.is_file():
                return resolved
            if is_path_like and not resolved.exists():
                raise InputValidationError(f"File not found within workspace: '{raw_str}'")
        except PathContainmentError as exc:
            raise InputValidationError(
                f"Path containment violation: path '{raw_str}' escapes workspace root."
            ) from exc
        except (ValueError, RuntimeError, OSError) as exc:
            if is_path_like:
                raise InputValidationError(f"Invalid workspace path '{raw_str}': {exc}") from exc
            return None
    else:
        p = Path(raw_str)
        if p.is_file():
            return p

    return None


def load_ontology_delta_dict(
    raw: dict[str, Any],
    *,
    allow_bootstrap: bool = False,
) -> OntologyDelta:
    """Validate an OntologyDelta dictionary without implicit bootstrap auto-enablement."""
    if not isinstance(raw, dict):
        raise InputValidationError(
            f"Expected dictionary root for OntologyDelta, got {type(raw).__name__}"
        )
    d = raw
    if "delta" in d and ("proposal_id" in d or "recommender_id" in d):
        d = d["delta"]
    ctx = {"allow_bootstrap": True} if allow_bootstrap else None
    try:
        return OntologyDelta.model_validate(d, context=ctx)
    except (ValueError, TypeError) as exc:
        raise InputValidationError(f"Invalid OntologyDelta dictionary: {exc}") from exc


def load_ontology_delta_from_bytes(
    raw_bytes: bytes,
    *,
    allow_bootstrap: bool = False,
) -> OntologyDelta:
    """Parse and validate an OntologyDelta from raw UTF-8 YAML/JSON bytes."""
    try:
        text = raw_bytes.decode("utf-8")
        parsed = yaml.safe_load(text)
    except (UnicodeDecodeError, yaml.YAMLError, ValueError, TypeError) as exc:
        raise InputValidationError(f"Failed to parse ontology delta YAML/JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise InputValidationError(
            f"Expected dictionary root for OntologyDelta, got {type(parsed).__name__}"
        )
    return load_ontology_delta_dict(parsed, allow_bootstrap=allow_bootstrap)


def load_ontology_delta(
    delta_or_path_or_content: Any,
    *,
    workspace_root: Path | None = None,
    allow_bootstrap: bool = False,
) -> OntologyDelta:
    """Load, parse, and validate an OntologyDelta from a model, dict, path, or inline text."""
    if isinstance(delta_or_path_or_content, OntologyDelta):
        if (
            not allow_bootstrap
            and delta_or_path_or_content.base_ontology_version == "1.0.0"
            and delta_or_path_or_content.target_ontology_version == "1.0.0"
        ):
            try:
                return OntologyDelta.model_validate(
                    delta_or_path_or_content.model_dump(mode="python"),
                    context=None,
                )
            except (ValueError, TypeError) as exc:
                raise InputValidationError(f"Invalid OntologyDelta payload: {exc}") from exc
        return delta_or_path_or_content

    if isinstance(delta_or_path_or_content, bytes):
        return load_ontology_delta_from_bytes(
            delta_or_path_or_content, allow_bootstrap=allow_bootstrap
        )

    if isinstance(delta_or_path_or_content, dict):
        return load_ontology_delta_dict(delta_or_path_or_content, allow_bootstrap=allow_bootstrap)

    if isinstance(delta_or_path_or_content, (str, Path)):
        raw_str = str(delta_or_path_or_content).strip()
        candidate_path = _resolve_candidate_file_path(raw_str, workspace_root=workspace_root)

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

        ctx = {"allow_bootstrap": True} if allow_bootstrap else None
        try:
            return OntologyDelta.model_validate(parsed, context=ctx)
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

    try:
        loaded = load_graph_a_source(
            base_or_path_or_content,
            workspace_root=workspace_root,
            allow_temp=True,
        )
        return loaded.ontology
    except PathContainmentError as exc:
        raise InputValidationError(
            f"Path containment violation: path '{base_or_path_or_content}' escapes workspace root."
        ) from exc
    except Exception as exc:
        if isinstance(exc, InputValidationError):
            raise
        raise InputValidationError(f"Failed to load base Graph A ontology: {exc}") from exc


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
        candidate_path = _resolve_candidate_file_path(raw_str, workspace_root=workspace_root)

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
    "load_ontology_delta_dict",
    "load_ontology_delta_from_bytes",
]
