"""Graph A Semantic Ontology source adapter and loader (ONT-01)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from opendv_core.models.graph_a import GraphAOntology
from opendv_core.paths import resolve_workspace_path
from opendv_sources.exceptions import (
    FormatError,
    SourceError,
    SourceNotFoundError,
)
from opendv_sources.models.specs import SourceSpec


class LoadedGraphA(NamedTuple):
    """Result of loading and validating a Graph A ontology source."""

    ontology: GraphAOntology
    sha256: str
    raw_bytes: bytes


def load_graph_a_source(
    source_spec_or_path: SourceSpec | str | Path | dict[str, Any],
    *,
    workspace_root: Path | None = None,
    base_dir: Path | None = None,
    allowed_roots: Sequence[Path] | None = None,
    allow_temp: bool = True,
) -> LoadedGraphA:
    """Load, hash, and validate an Enterprise Canonical Business Concept (CBC) Graph A ontology.

    Supports:
      - `SourceSpec` instance with adapter='file', 'inline', or custom plugins
      - Serialized `SourceSpec` dict (containing key "adapter")
      - Direct dictionary representation of `GraphAOntology`
      - Filesystem path (`Path` or `str`) with workspace containment verification
      - Inline YAML or JSON string

    Args:
        source_spec_or_path: Specification, file path, dictionary, or inline document.
        workspace_root: Workspace root directory for path containment.
        base_dir: Optional base directory for relative path resolution.
        allowed_roots: Optional additional permitted root directories.
        allow_temp: Whether to permit paths inside the system temp directory.

    Returns:
        `LoadedGraphA` containing the validated `GraphAOntology`, raw bytes `sha256` hex,
        and `raw_bytes`.

    Raises:
        PathContainmentError: If path violates workspace jail boundaries.
        SourceNotFoundError: If the target file path does not exist.
        FormatError: If parsing fails or the root document is not an object mapping.
        ValidationError: If the parsed document fails `GraphAOntology` schema validation.
        SourceError: If the source specification is malformed or unsupported.
    """
    raw_dict: dict[str, Any] | None = None
    raw_bytes: bytes

    if isinstance(source_spec_or_path, str) and not source_spec_or_path.strip():
        raise FormatError("Expected non-empty Graph A ontology content, got empty string.")

    if isinstance(source_spec_or_path, SourceSpec):
        is_inline = (
            source_spec_or_path.adapter == "inline"
            or source_spec_or_path.inline_content is not None
        )
        if is_inline:
            content = source_spec_or_path.inline_content
            if content is None:
                content = source_spec_or_path.options.get(
                    "file_content"
                ) or source_spec_or_path.options.get("data")
            if content is None:
                raise SourceError(
                    "Inline SourceSpec requires 'inline_content' or options['file_content']"
                )
            raw_bytes = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        elif source_spec_or_path.uri:
            resolved_p = resolve_workspace_path(
                source_spec_or_path.uri,
                workspace_root=workspace_root,
                base_dir=base_dir,
                allowed_roots=allowed_roots,
                allow_temp=allow_temp,
            )
            if not resolved_p.is_file():
                raise SourceNotFoundError(f"Graph A ontology file not found: '{resolved_p}'")
            raw_bytes = resolved_p.read_bytes()
        elif source_spec_or_path.options.get("path") or source_spec_or_path.options.get(
            "file_path"
        ):
            target_p = source_spec_or_path.options.get("path") or source_spec_or_path.options.get(
                "file_path"
            )
            assert target_p is not None
            resolved_p = resolve_workspace_path(
                str(target_p),
                workspace_root=workspace_root,
                base_dir=base_dir,
                allowed_roots=allowed_roots,
                allow_temp=allow_temp,
            )
            if not resolved_p.is_file():
                raise SourceNotFoundError(f"Graph A ontology file not found: '{resolved_p}'")
            raw_bytes = resolved_p.read_bytes()
        else:
            raise SourceError(
                f"Unsupported or missing URI/content in SourceSpec: {source_spec_or_path}"
            )

    elif isinstance(source_spec_or_path, dict):
        if "adapter" in source_spec_or_path:
            spec = SourceSpec.model_validate(source_spec_or_path)
            return load_graph_a_source(
                spec,
                workspace_root=workspace_root,
                base_dir=base_dir,
                allowed_roots=allowed_roots,
                allow_temp=allow_temp,
            )
        raw_dict = source_spec_or_path
        raw_bytes = yaml.safe_dump(raw_dict, sort_keys=False).encode("utf-8")

    elif isinstance(source_spec_or_path, Path):
        resolved_p = resolve_workspace_path(
            source_spec_or_path,
            workspace_root=workspace_root,
            base_dir=base_dir,
            allowed_roots=allowed_roots,
            allow_temp=allow_temp,
        )
        if not resolved_p.is_file():
            raise SourceNotFoundError(f"Graph A ontology file not found: '{resolved_p}'")
        raw_bytes = resolved_p.read_bytes()

    elif isinstance(source_spec_or_path, str):
        is_path = "\n" not in source_spec_or_path and not source_spec_or_path.strip().startswith(
            ("{", "[")
        )
        if is_path:
            resolved_p = resolve_workspace_path(
                source_spec_or_path,
                workspace_root=workspace_root,
                base_dir=base_dir,
                allowed_roots=allowed_roots,
                allow_temp=allow_temp,
            )
            if not resolved_p.is_file():
                raise SourceNotFoundError(f"Graph A ontology file not found: '{resolved_p}'")
            raw_bytes = resolved_p.read_bytes()
        else:
            raw_bytes = source_spec_or_path.encode("utf-8")
    else:
        type_name = type(source_spec_or_path).__name__
        raise SourceError(f"Unsupported input type for Graph A source: {type_name}")

    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    if raw_dict is None:
        try:
            text = raw_bytes.decode("utf-8")
            is_json = text.strip().startswith(("{", "["))
            loaded = json.loads(text) if is_json else yaml.safe_load(text)
        except Exception as exc:
            raise FormatError(f"Failed to parse Graph A ontology content: {exc}") from exc

        if not isinstance(loaded, dict):
            type_name = type(loaded).__name__
            raise FormatError(f"Expected mapping object for Graph A ontology, got {type_name}")
        raw_dict = loaded

    ontology = GraphAOntology.model_validate(raw_dict)
    return LoadedGraphA(ontology=ontology, sha256=sha256, raw_bytes=raw_bytes)


__all__ = ["LoadedGraphA", "load_graph_a_source"]
