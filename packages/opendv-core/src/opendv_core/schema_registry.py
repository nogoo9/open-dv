"""Draft 2020-12 JSON Schema Registry and Artifact Validator for Open-DV (v6.0).

Loads and validates the normative JSON schemas in `/schemas`:
- `schemas/no-mistake-dv2-contract-v5.schema.json`
- `schemas/dv2-canonical-ir.schema.json`
- `schemas/vault-observability-event.schema.json`

Provides dual validation (Draft 2020-12 JSON Schema + strict Pydantic v2 models)
for contracts, Canonical IRs, Graph A ontologies, Graph B statistical profiles,
and observability telemetry events.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import jsonschema  # type: ignore[import-untyped]
import yaml

from opendv_core.models.canonical_ir import CanonicalIR
from opendv_core.models.contract_v5 import NoMistakeDataVault2ContractV5
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.models.graph_b import GraphBProfile
from opendv_core.models.observability import VaultObservabilityEvent

CONTRACT_V5_SCHEMA_FILENAME: Final[str] = "no-mistake-dv2-contract-v5.schema.json"
CANONICAL_IR_SCHEMA_FILENAME: Final[str] = "dv2-canonical-ir.schema.json"
OBSERVABILITY_EVENT_SCHEMA_FILENAME: Final[str] = "vault-observability-event.schema.json"

NORMATIVE_SCHEMA_FILENAMES: Final[tuple[str, ...]] = (
    CONTRACT_V5_SCHEMA_FILENAME,
    CANONICAL_IR_SCHEMA_FILENAME,
    OBSERVABILITY_EVENT_SCHEMA_FILENAME,
)

SCHEMA_ALIASES: Final[dict[str, str]] = {
    "contract": CONTRACT_V5_SCHEMA_FILENAME,
    "contract_v5": CONTRACT_V5_SCHEMA_FILENAME,
    "no-mistake-dv2-contract-v5": CONTRACT_V5_SCHEMA_FILENAME,
    CONTRACT_V5_SCHEMA_FILENAME: CONTRACT_V5_SCHEMA_FILENAME,
    "canonical_ir": CANONICAL_IR_SCHEMA_FILENAME,
    "dv2-canonical-ir": CANONICAL_IR_SCHEMA_FILENAME,
    CANONICAL_IR_SCHEMA_FILENAME: CANONICAL_IR_SCHEMA_FILENAME,
    "observability": OBSERVABILITY_EVENT_SCHEMA_FILENAME,
    "observability_event": OBSERVABILITY_EVENT_SCHEMA_FILENAME,
    "vault-observability-event": OBSERVABILITY_EVENT_SCHEMA_FILENAME,
    OBSERVABILITY_EVENT_SCHEMA_FILENAME: OBSERVABILITY_EVENT_SCHEMA_FILENAME,
}


def find_workspace_root(start_path: Path | None = None) -> Path:
    """Locate the monorepo workspace root containing `schemas/` and `examples/`."""
    candidates: list[Path] = []
    if start_path is not None:
        candidates.append(start_path.resolve())
    candidates.append(Path(__file__).resolve())
    candidates.append(Path.cwd().resolve())

    for base in candidates:
        current = base if base.is_dir() else base.parent
        for directory in (current, *current.parents):
            if (directory / "schemas").is_dir() and (
                directory / "schemas" / CONTRACT_V5_SCHEMA_FILENAME
            ).is_file():
                return directory

    msg = "Unable to locate Open-DV workspace root containing 'schemas/' directory"
    raise FileNotFoundError(msg)


def get_schemas_dir(workspace_root: Path | None = None) -> Path:
    """Return the absolute path to the `/schemas` directory."""
    root = workspace_root if workspace_root is not None else find_workspace_root()
    return root / "schemas"


def get_examples_dir(workspace_root: Path | None = None) -> Path:
    """Return the absolute path to the `/examples` directory."""
    root = workspace_root if workspace_root is not None else find_workspace_root()
    return root / "examples"


def load_structured_file(path_or_payload: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    """Load a dictionary payload from a Path, file path string, raw YAML/JSON string, or Mapping."""
    if isinstance(path_or_payload, Mapping):
        return dict(path_or_payload)

    if isinstance(path_or_payload, Path):
        file_path = path_or_payload
    else:
        stripped = path_or_payload.strip()
        if stripped.startswith(("{", "[")) or "\n" in stripped:
            parsed = yaml.safe_load(stripped)
            if not isinstance(parsed, dict):
                msg = "Parsed structured content must be a JSON/YAML object mapping"
                raise ValueError(msg)
            return parsed
        file_path = Path(path_or_payload)

    text = file_path.read_text(encoding="utf-8")
    loaded = json.loads(text) if file_path.suffix.lower() == ".json" else yaml.safe_load(text)

    if not isinstance(loaded, dict):
        msg = f"Expected object mapping in '{file_path}', got {type(loaded).__name__}"
        raise ValueError(msg)
    return loaded


class SchemaRegistry:
    """Draft 2020-12 JSON Schema registry and validator for Open-DV."""

    def __init__(self, schemas_dir: Path | None = None) -> None:
        self._schemas_dir = schemas_dir.resolve() if schemas_dir is not None else get_schemas_dir()
        self._schema_cache: dict[str, dict[str, Any]] = {}
        self._validator_cache: dict[str, Any] = {}

    @property
    def schemas_dir(self) -> Path:
        """Directory containing the normative `.schema.json` files."""
        return self._schemas_dir

    def resolve_schema_filename(self, name_or_alias: str) -> str:
        """Resolve a short alias or filename to the canonical `.schema.json` filename."""
        if name_or_alias in SCHEMA_ALIASES:
            return SCHEMA_ALIASES[name_or_alias]
        candidate = self._schemas_dir / name_or_alias
        if candidate.is_file():
            return name_or_alias
        msg = (
            f"Unknown schema '{name_or_alias}'. Known aliases/files: "
            f"{sorted(SCHEMA_ALIASES.keys())}"
        )
        raise KeyError(msg)

    def load_schema(self, name_or_alias: str) -> dict[str, Any]:
        """Load and verify a Draft 2020-12 JSON schema by filename or alias."""
        filename = self.resolve_schema_filename(name_or_alias)
        if filename not in self._schema_cache:
            schema_path = self._schemas_dir / filename
            if not schema_path.is_file():
                msg = f"Schema file not found: {schema_path}"
                raise FileNotFoundError(msg)
            raw_schema = json.loads(schema_path.read_text(encoding="utf-8"))
            if not isinstance(raw_schema, dict):
                msg = f"Schema '{schema_path}' root must be a JSON object"
                raise ValueError(msg)
            jsonschema.Draft202012Validator.check_schema(raw_schema)
            self._schema_cache[filename] = raw_schema
        return self._schema_cache[filename]

    def get_validator(self, name_or_alias: str) -> Any:
        """Return a compiled `Draft202012Validator` instance for the given schema."""
        filename = self.resolve_schema_filename(name_or_alias)
        if filename not in self._validator_cache:
            schema = self.load_schema(filename)
            self._validator_cache[filename] = jsonschema.Draft202012Validator(
                schema,
                format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
            )
        return self._validator_cache[filename]

    def check_all_schemas(self) -> dict[str, dict[str, Any]]:
        """Check all normative JSON schemas in `schemas/` against Draft 2020-12."""
        results: dict[str, dict[str, Any]] = {}
        for filename in NORMATIVE_SCHEMA_FILENAMES:
            results[filename] = self.load_schema(filename)
        for extra_path in sorted(self._schemas_dir.glob("*.json")):
            if extra_path.name not in results:
                results[extra_path.name] = self.load_schema(extra_path.name)
        return results

    def validate_payload(self, name_or_alias: str, payload: Mapping[str, Any]) -> None:
        """Validate a mapping payload against the named Draft 2020-12 JSON schema."""
        validator = self.get_validator(name_or_alias)
        validator.validate(dict(payload))

    def validate_contract_v5(
        self,
        path_or_payload: Mapping[str, Any] | str | Path,
    ) -> NoMistakeDataVault2ContractV5:
        """Validate a v5.0 contract against both Draft 2020-12 JSON Schema and Pydantic v2."""
        data = load_structured_file(path_or_payload)
        self.validate_payload(CONTRACT_V5_SCHEMA_FILENAME, data)
        return NoMistakeDataVault2ContractV5.model_validate(data)

    def validate_canonical_ir(
        self,
        path_or_payload: Mapping[str, Any] | str | Path,
    ) -> CanonicalIR:
        """Validate a Canonical IR against both Draft 2020-12 JSON Schema and Pydantic v2."""
        data = load_structured_file(path_or_payload)
        self.validate_payload(CANONICAL_IR_SCHEMA_FILENAME, data)
        return CanonicalIR.model_validate(data)

    def validate_observability_event(
        self,
        path_or_payload: Mapping[str, Any] | str | Path,
    ) -> VaultObservabilityEvent:
        """Validate a telemetry event against both Draft 2020-12 JSON Schema and Pydantic v2."""
        data = load_structured_file(path_or_payload)
        self.validate_payload(OBSERVABILITY_EVENT_SCHEMA_FILENAME, data)
        return VaultObservabilityEvent.model_validate(data)

    def validate_graph_a(
        self,
        path_or_payload: Mapping[str, Any] | str | Path,
    ) -> GraphAOntology:
        """Validate a Graph A Semantic Ontology artifact with Pydantic v2."""
        data = load_structured_file(path_or_payload)
        return GraphAOntology.model_validate(data)

    def validate_graph_b(
        self,
        path_or_payload: Mapping[str, Any] | str | Path,
    ) -> GraphBProfile:
        """Validate a Graph B Statistical Profile artifact with Pydantic v2."""
        data = load_structured_file(path_or_payload)
        return GraphBProfile.model_validate(data)


_DEFAULT_REGISTRY: SchemaRegistry | None = None


def get_default_schema_registry() -> SchemaRegistry:
    """Return a singleton `SchemaRegistry` initialized for the current workspace."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = SchemaRegistry()
    return _DEFAULT_REGISTRY


def validate_contract_v5(
    path_or_payload: Mapping[str, Any] | str | Path,
) -> NoMistakeDataVault2ContractV5:
    """Validate a v5.0 model contract using the default `SchemaRegistry`."""
    return get_default_schema_registry().validate_contract_v5(path_or_payload)


def validate_canonical_ir(
    path_or_payload: Mapping[str, Any] | str | Path,
) -> CanonicalIR:
    """Validate a Canonical IR using the default `SchemaRegistry`."""
    return get_default_schema_registry().validate_canonical_ir(path_or_payload)


def validate_observability_event(
    path_or_payload: Mapping[str, Any] | str | Path,
) -> VaultObservabilityEvent:
    """Validate a `VaultObservabilityEvent` using the default `SchemaRegistry`."""
    return get_default_schema_registry().validate_observability_event(path_or_payload)


def validate_graph_a(
    path_or_payload: Mapping[str, Any] | str | Path,
) -> GraphAOntology:
    """Validate a Graph A ontology artifact using the default `SchemaRegistry`."""
    return get_default_schema_registry().validate_graph_a(path_or_payload)


def validate_graph_b(
    path_or_payload: Mapping[str, Any] | str | Path,
) -> GraphBProfile:
    """Validate a Graph B profile artifact using the default `SchemaRegistry`."""
    return get_default_schema_registry().validate_graph_b(path_or_payload)


def validate_all_examples(workspace_root: Path | None = None) -> dict[str, Any]:
    """Validate all normative artifacts in `examples/` and `examples/private_equity/`."""
    root = workspace_root if workspace_root is not None else find_workspace_root()
    registry = SchemaRegistry(schemas_dir=root / "schemas")
    examples_dir = root / "examples"

    validated: dict[str, Any] = {}
    validated["examples/dv_model_contract.yaml"] = registry.validate_contract_v5(
        examples_dir / "dv_model_contract.yaml"
    )
    validated["examples/private_equity/dv_pe_model_contract.yaml"] = registry.validate_contract_v5(
        examples_dir / "private_equity" / "dv_pe_model_contract.yaml"
    )
    validated["examples/private_equity/pe_canonical_ir.json"] = registry.validate_canonical_ir(
        examples_dir / "private_equity" / "pe_canonical_ir.json"
    )
    validated["examples/private_equity/pe_graph_a_ontology.yaml"] = registry.validate_graph_a(
        examples_dir / "private_equity" / "pe_graph_a_ontology.yaml"
    )
    validated["examples/private_equity/pe_graph_b_profile.json"] = registry.validate_graph_b(
        examples_dir / "private_equity" / "pe_graph_b_profile.json"
    )
    return validated


__all__ = [
    "CANONICAL_IR_SCHEMA_FILENAME",
    "CONTRACT_V5_SCHEMA_FILENAME",
    "NORMATIVE_SCHEMA_FILENAMES",
    "OBSERVABILITY_EVENT_SCHEMA_FILENAME",
    "SCHEMA_ALIASES",
    "SchemaRegistry",
    "find_workspace_root",
    "get_default_schema_registry",
    "get_examples_dir",
    "get_schemas_dir",
    "load_structured_file",
    "validate_all_examples",
    "validate_canonical_ir",
    "validate_contract_v5",
    "validate_graph_a",
    "validate_graph_b",
    "validate_observability_event",
]
