"""Workspace seed ontology discovery and Step 1 initial ontology resolution."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Final

import yaml
from pydantic import ValidationError

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.ontology import GraphAOntology
from opendv.models.project import validate_domain_namespace
from opendv.models.wire_dtos_discovery import ProjectInitReq, WorkspaceSeedSummary
from opendv.sources.starter_packs import get_starter_pack_ontology

_EXCLUDED_SEED_DIRS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".opendv",
        "worktrees",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "dist",
        "build",
    }
)
_SEED_EXTENSIONS: Final[frozenset[str]] = frozenset({".yaml", ".yml", ".json"})
_MAX_SEED_FILE_BYTES: Final[int] = 2 * 1024 * 1024


def _parse_ontology_mapping(raw_text: str, *, fmt: str, label: str) -> GraphAOntology:
    try:
        loaded = json.loads(raw_text) if fmt == "json" else yaml.safe_load(raw_text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise OpenDVError(
            f"Invalid {fmt.upper()} ontology payload in {label}: {exc}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from exc
    if not isinstance(loaded, dict):
        raise OpenDVError(
            f"Ontology payload in {label} must be a mapping/object.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    try:
        return GraphAOntology.model_validate(loaded)
    except ValidationError as exc:
        raise OpenDVError(
            f"Ontology schema validation failed for {label}: {exc}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from exc


def discover_workspace_seeds(
    workspace_root: Path,
    *,
    domain_namespace: str | None = None,
) -> list[tuple[WorkspaceSeedSummary, GraphAOntology]]:
    """Scan workspace_root for domain-scoped GraphAOntology YAML/JSON seed files."""
    if domain_namespace is not None:
        try:
            validate_domain_namespace(domain_namespace)
        except ValueError as exc:
            raise OpenDVError(str(exc), error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT) from exc

    root = workspace_root.resolve()
    if not root.is_dir():
        return []

    discovered: list[tuple[WorkspaceSeedSummary, GraphAOntology]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in _EXCLUDED_SEED_DIRS and not (Path(dirpath) / d).is_symlink()
        )
        cur_dir = Path(dirpath)
        for fname in sorted(filenames):
            fpath = cur_dir / fname
            if fpath.is_symlink():
                continue
            ext = fpath.suffix.lower()
            if ext not in _SEED_EXTENSIONS:
                continue
            try:
                if fpath.stat().st_size > _MAX_SEED_FILE_BYTES:
                    continue
                raw_text = fpath.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            fmt = "json" if ext == ".json" else "yaml"
            try:
                loaded = json.loads(raw_text) if fmt == "json" else yaml.safe_load(raw_text)
            except (json.JSONDecodeError, yaml.YAMLError):
                continue
            if not isinstance(loaded, dict) or (
                "ontology_version" not in loaded or "concepts" not in loaded
            ):
                continue
            try:
                ont = GraphAOntology.model_validate(loaded)
            except ValidationError:
                continue

            if domain_namespace is not None and ont.domain_namespace != domain_namespace:
                continue
            rel_posix = fpath.relative_to(root).as_posix()
            summary = WorkspaceSeedSummary(
                seed_id=rel_posix,
                domain_namespace=ont.domain_namespace,
                relpath=rel_posix,
                concept_count=len(ont.concepts),
            )
            discovered.append((summary, ont))

    discovered.sort(key=lambda item: item[0].seed_id)
    return discovered


def resolve_initial_ontology(
    req: ProjectInitReq,
    workspace_root: Path,
) -> tuple[GraphAOntology, str]:
    """Resolve initial GraphAOntology and init_mode across Blank/StarterPack/Seed/Baseline."""
    active_modes = sum(
        1
        for flag in (
            req.starter_pack_id is not None,
            req.workspace_seed_id is not None,
            req.baseline_content is not None,
        )
        if flag
    )
    if active_modes > 1:
        raise OpenDVError(
            "Provide at most one of starter_pack_id, workspace_seed_id, or baseline_content.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    if req.starter_pack_id is not None:
        pack_ont = get_starter_pack_ontology(req.starter_pack_id)
        return pack_ont.model_copy(update={"domain_namespace": req.domain_namespace}), (
            f"starter_pack:{req.starter_pack_id}"
        )

    if req.workspace_seed_id is not None:
        seeds = discover_workspace_seeds(workspace_root)
        matched = next(
            (ont for summary, ont in seeds if summary.seed_id == req.workspace_seed_id),
            None,
        )
        if matched is None:
            raise OpenDVError(
                f"Workspace seed '{req.workspace_seed_id}' not found.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        if matched.domain_namespace != req.domain_namespace:
            raise OpenDVError(
                f"Workspace seed '{req.workspace_seed_id}' namespace "
                f"'{matched.domain_namespace}' does not match '{req.domain_namespace}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        return matched, f"workspace_seed:{req.workspace_seed_id}"

    if req.baseline_content is not None:
        if not req.baseline_content.strip():
            raise OpenDVError(
                "baseline_content cannot be empty.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if req.baseline_format not in ("yaml", "json"):
            raise OpenDVError(
                f"Unsupported baseline_format '{req.baseline_format}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        ont = _parse_ontology_mapping(
            req.baseline_content,
            fmt=req.baseline_format,
            label="baseline_content",
        )
        return ont.model_copy(update={"domain_namespace": req.domain_namespace}), (
            f"baseline:{req.baseline_format}"
        )

    return (
        GraphAOntology(ontology_version="0.0.0", domain_namespace=req.domain_namespace),
        "blank",
    )


__all__ = [
    "discover_workspace_seeds",
    "resolve_initial_ontology",
]
