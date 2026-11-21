"""Deployment state provider adapters and workspace contract discovery (ONT-EVO-001)."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

from opendv_contracts.errors import ErrorCode
from opendv_contracts.spi.deployment import DeploymentStateProvider
from opendv_service.ontology.validator_models import OntologyGovernanceError

_SKIP_WORKSPACE_SCAN_DIRS: frozenset[str] = frozenset(
    {
        ".opendv",
        ".git",
        "node_modules",
        ".venv",
        "dist",
        "build",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
    }
)
_MONOREPO_SOURCE_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "packages",
        "apps",
        "tests",
        "vendor",
        "docs",
        "scripts",
        "schemas",
        "examples",
        "vectors",
        "worktrees",
        "temp_state",
        "test_tmp",
        "tmp_ws",
        "artifacts",
    }
)


def _is_reparse_or_symlink(path: Path) -> bool:
    """Return True when `path` is a symlink or Windows NTFS junction/reparse point."""
    with contextlib.suppress(OSError):
        if path.is_symlink():
            return True
        is_junc = getattr(path, "is_junction", None)
        if callable(is_junc) and bool(is_junc()):
            return True
    return False


class _MergedDeploymentProvider:
    """Immutable in-memory `DeploymentStateProvider` merging workspace contract states."""

    def __init__(
        self,
        concepts: set[str] | frozenset[str],
        tables: set[str] | frozenset[str],
        entities: set[str] | frozenset[str],
    ) -> None:
        self._concepts = frozenset(concepts)
        self._tables = frozenset(tables)
        self._entities = frozenset(entities)

    def get_deployed_concepts(self) -> frozenset[str]:
        return self._concepts

    def get_deployed_tables(self) -> frozenset[str]:
        return self._tables

    def get_deployed_entities(self) -> frozenset[str]:
        return self._entities


class _ContractDeploymentProvider:
    """Adapter turning a v5 deployed contract object/dict into a DeploymentStateProvider."""

    def __init__(self, deployed_contract: Any) -> None:
        if hasattr(deployed_contract, "model_dump"):
            doc = deployed_contract.model_dump(mode="json")
        elif isinstance(deployed_contract, dict):
            doc = deployed_contract
        else:
            raise ValueError("Invalid Data Vault contract payload")
        if not isinstance(doc, dict):
            raise ValueError("Invalid Data Vault contract payload")

        for col_key in (
            "hubs",
            "links",
            "satellites",
            "reference_tables",
            "deployed_tables",
            "deployed_concepts",
            "deployed_entities",
        ):
            col_val = doc.get(col_key)
            if col_val is not None and not isinstance(col_val, (list, tuple, set, frozenset)):
                raise ValueError("Invalid Data Vault contract collection type")

        tables_val = doc.get("tables")
        if tables_val is not None and not isinstance(
            tables_val, (list, tuple, set, frozenset, dict)
        ):
            raise ValueError("Invalid Data Vault contract collection type")

        self._concepts: set[str] = set()
        self._entities: set[str] = set()
        self._tables: set[str] = set()

        for hub in doc.get("hubs") or []:
            if isinstance(hub, dict):
                name = hub.get("name") or hub.get("table_name")
                if isinstance(name, str) and name.strip():
                    self._tables.add(name.strip())
                uri = hub.get("ontology_concept_uri") or hub.get("concept_uri")
                if isinstance(uri, str) and uri.strip():
                    clean_uri = uri.strip()
                    self._concepts.add(clean_uri)
                    self._entities.add(clean_uri)
            elif isinstance(hub, str):
                if hub.strip():
                    self._tables.add(hub.strip())
            else:
                raise ValueError("Invalid Data Vault contract collection type")

        for link in doc.get("links") or []:
            if isinstance(link, dict):
                name = link.get("name") or link.get("table_name")
                if isinstance(name, str) and name.strip():
                    self._tables.add(name.strip())
                uri = link.get("ontology_concept_uri") or link.get("relationship_uri")
                if isinstance(uri, str) and uri.strip():
                    self._entities.add(uri.strip())
            elif isinstance(link, str):
                if link.strip():
                    self._tables.add(link.strip())
            else:
                raise ValueError("Invalid Data Vault contract collection type")

        for sat in doc.get("satellites") or []:
            if isinstance(sat, dict):
                name = sat.get("name") or sat.get("table_name")
                if isinstance(name, str) and name.strip():
                    self._tables.add(name.strip())
                uri = sat.get("ontology_concept_uri") or sat.get("concept_uri")
                if isinstance(uri, str) and uri.strip():
                    clean_uri = uri.strip()
                    self._concepts.add(clean_uri)
                    self._entities.add(clean_uri)
            elif isinstance(sat, str):
                if sat.strip():
                    self._tables.add(sat.strip())
            else:
                raise ValueError("Invalid Data Vault contract collection type")

        for ref in doc.get("reference_tables") or []:
            if isinstance(ref, dict):
                name = ref.get("name") or ref.get("table_name")
                if isinstance(name, str) and name.strip():
                    self._tables.add(name.strip())
                uri = ref.get("ontology_concept_uri") or ref.get("enum_uri")
                if isinstance(uri, str) and uri.strip():
                    self._entities.add(uri.strip())
            elif isinstance(ref, str):
                if ref.strip():
                    self._tables.add(ref.strip())
            else:
                raise ValueError("Invalid Data Vault contract collection type")

        for dep_concept in doc.get("deployed_concepts") or []:
            if not isinstance(dep_concept, str):
                raise ValueError("Invalid Data Vault contract collection type")
            if dep_concept.strip():
                self._concepts.add(dep_concept.strip())

        for dep_entity in doc.get("deployed_entities") or []:
            if not isinstance(dep_entity, str):
                raise ValueError("Invalid Data Vault contract collection type")
            if dep_entity.strip():
                self._entities.add(dep_entity.strip())

        for dep_table in doc.get("deployed_tables") or []:
            if isinstance(dep_table, str):
                if dep_table.strip():
                    self._tables.add(dep_table.strip())
            elif isinstance(dep_table, dict):
                self._ingest_table_entry(dep_table)
            else:
                raise ValueError("Invalid Data Vault contract collection type")

        if isinstance(tables_val, dict):
            for tbl_key, tbl_entry in tables_val.items():
                if isinstance(tbl_key, str) and tbl_key.strip():
                    self._tables.add(tbl_key.strip())
                if isinstance(tbl_entry, dict):
                    self._ingest_table_entry(tbl_entry)
                elif tbl_entry is not None:
                    raise ValueError("Invalid Data Vault contract collection type")
        elif tables_val is not None:
            for tbl_item in tables_val:
                if isinstance(tbl_item, dict):
                    self._ingest_table_entry(tbl_item)
                elif isinstance(tbl_item, str):
                    if tbl_item.strip():
                        self._tables.add(tbl_item.strip())
                else:
                    raise ValueError("Invalid Data Vault contract collection type")

    def _ingest_table_entry(self, entry: dict[str, Any]) -> None:
        name = entry.get("name") or entry.get("table_name")
        if isinstance(name, str) and name.strip():
            self._tables.add(name.strip())
        c_uri = entry.get("ontology_concept_uri") or entry.get("concept_uri")
        if isinstance(c_uri, str) and c_uri.strip():
            clean_c = c_uri.strip()
            self._concepts.add(clean_c)
            self._entities.add(clean_c)
        for rel_key in ("relationship_uri", "enum_uri"):
            r_uri = entry.get(rel_key)
            if isinstance(r_uri, str) and r_uri.strip():
                self._entities.add(r_uri.strip())

    def get_deployed_concepts(self) -> set[str]:
        return set(self._concepts)

    def get_deployed_tables(self) -> set[str]:
        return set(self._tables)

    def get_deployed_entities(self) -> set[str]:
        return set(self._entities)


def build_deployment_provider_from_contract(
    deployed_contract: Any,
) -> DeploymentStateProvider | None:
    """Construct a DeploymentStateProvider from a legacy v5 deployed contract payload."""
    if deployed_contract is None:
        return None
    return _ContractDeploymentProvider(deployed_contract)  # type: ignore[return-value]


def build_workspace_deployment_provider(
    workspace_root: Path | str | None,
) -> DeploymentStateProvider | None:
    """Recursively inspect workspace for deployed Data Vault contracts (ONT-EVO-001)."""
    if workspace_root is None:
        return None
    ws_root = Path(workspace_root).resolve()
    if not ws_root.is_dir():
        return None

    skip_dirs = set(_SKIP_WORKSPACE_SCAN_DIRS)
    if (ws_root / "pyproject.toml").is_file() and (ws_root / "packages" / "opendv-core").is_dir():
        skip_dirs.update(_MONOREPO_SOURCE_SKIP_DIRS)

    candidates: list[Path] = []
    for root_str, dirnames, filenames in os.walk(ws_root, topdown=True, followlinks=False):
        root_p = Path(root_str)
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not d.startswith(".")
            and d not in skip_dirs
            and not _is_reparse_or_symlink(root_p / d)
        )
        for fname in sorted(filenames):
            if not fname.lower().endswith((".yaml", ".yml", ".json")):
                continue
            fpath = root_p / fname
            if _is_reparse_or_symlink(fpath) or not fpath.is_file():
                continue
            candidates.append(fpath)

    merged_concepts: set[str] = set()
    merged_tables: set[str] = set()
    merged_entities: set[str] = set()
    found_any = False

    for cand in candidates:
        rel_parts = cand.relative_to(ws_root).parts
        in_contracts_dir = any(part.lower() == "contracts" for part in rel_parts[:-1])
        is_contract_file = in_contracts_dir or ("contract" in cand.name.lower())

        try:
            raw_text = cand.read_text(encoding="utf-8")
            data = (
                json.loads(raw_text) if cand.suffix.lower() == ".json" else yaml.safe_load(raw_text)
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
            if is_contract_file:
                raise OntologyGovernanceError(
                    f"Failed to parse workspace contract '{cand.relative_to(ws_root)}': {exc}",
                    error_code=ErrorCode.INVALID_WORKSPACE_ONTOLOGY,
                    status_code=422,
                    exit_code=4,
                ) from exc
            continue

        if not isinstance(data, dict):
            if is_contract_file:
                rel_p = cand.relative_to(ws_root)
                raise OntologyGovernanceError(
                    f"Workspace contract '{rel_p}' must be a YAML/JSON mapping.",
                    error_code=ErrorCode.INVALID_WORKSPACE_ONTOLOGY,
                    status_code=422,
                    exit_code=4,
                )
            continue

        if "canonical_business_concepts" in data:
            continue

        is_dv_contract = any(
            k in data
            for k in (
                "tables",
                "hubs",
                "links",
                "satellites",
                "reference_tables",
                "deployed_tables",
                "deployed_concepts",
                "deployed_entities",
            )
        ) or ("contract_version" in data and "canonical_business_concepts" not in data)
        if not is_dv_contract:
            continue

        try:
            prov = build_deployment_provider_from_contract(data)
        except (ValueError, TypeError, KeyError) as exc:
            if is_contract_file:
                rel_p = cand.relative_to(ws_root)
                raise OntologyGovernanceError(
                    f"Invalid Data Vault contract structure in '{rel_p}': {exc}",
                    error_code=ErrorCode.INVALID_WORKSPACE_ONTOLOGY,
                    status_code=422,
                    exit_code=4,
                ) from exc
            continue

        if prov is None:
            continue
        merged_concepts.update(prov.get_deployed_concepts())
        merged_tables.update(prov.get_deployed_tables())
        merged_entities.update(prov.get_deployed_entities())
        found_any = True

    if not found_any:
        return None
    return _MergedDeploymentProvider(  # type: ignore[return-value]
        concepts=merged_concepts,
        tables=merged_tables,
        entities=merged_entities,
    )


def _extract_deployed_contract_mappings(
    deployed_contract: Any,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Extract (concept_uri -> hub), (rel_uri -> link), (enum_uri -> ref_table)."""
    deployed_hubs: dict[str, str] = {}
    deployed_links: dict[str, str] = {}
    deployed_refs: dict[str, str] = {}

    if deployed_contract is None:
        return deployed_hubs, deployed_links, deployed_refs

    doc = (
        deployed_contract.model_dump(mode="json")
        if hasattr(deployed_contract, "model_dump")
        else (deployed_contract if isinstance(deployed_contract, dict) else {})
    )

    for hub in doc.get("hubs", []):
        if isinstance(hub, dict) and "ontology_concept_uri" in hub and "name" in hub:
            deployed_hubs[hub["ontology_concept_uri"]] = hub["name"]

    for link in doc.get("links", []):
        if isinstance(link, dict) and "ontology_concept_uri" in link and "name" in link:
            deployed_links[link["ontology_concept_uri"]] = link["name"]

    for ref in doc.get("reference_tables", []):
        if isinstance(ref, dict) and "name" in ref:
            uri = ref.get("ontology_concept_uri") or ref.get("enum_uri")
            if uri:
                deployed_refs[uri] = ref["name"]

    return deployed_hubs, deployed_links, deployed_refs


__all__ = [
    "_ContractDeploymentProvider",
    "_MergedDeploymentProvider",
    "_extract_deployed_contract_mappings",
    "build_deployment_provider_from_contract",
    "build_workspace_deployment_provider",
]
