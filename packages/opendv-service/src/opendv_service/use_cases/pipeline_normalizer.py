"""Dual blueprint ingress normalizer for single-run pipeline execution (ADR-017).

Provides normalization of incoming PipelineInputDTO / PipelineValidateRequest / dict
into resolved (VaultBlueprint, Contract, Sources, UnstructuredDocuments).
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from opendv_contracts.dtos.blueprint import VaultBlueprintDTO
from opendv_contracts.dtos.pipeline import (
    PipelineInputDTO,
    PipelineValidateRequest,
)
from opendv_core.models.contract_v5 import NoMistakeDataVault2ContractV5
from opendv_core.models.vault_blueprint import VaultBlueprint, create_vault_blueprint
from opendv_core.paths import resolve_workspace_path


class DualBlueprintIngressNormalizer:
    """Normalizes dual blueprint ingress (VaultBlueprint vs ontology + contract) per ADR-017."""

    @classmethod
    def normalize(
        cls,
        input_data: PipelineInputDTO | PipelineValidateRequest | dict[str, Any],
        base_dir: Path | None = None,
        *,
        is_server: bool = False,
    ) -> tuple[
        VaultBlueprint | VaultBlueprintDTO | dict[str, Any] | None,
        NoMistakeDataVault2ContractV5 | dict[str, Any] | None,
        list[Any],
        list[Any],
    ]:
        """Normalize ingress into (VaultBlueprint, Contract, Sources, UnstructuredDocuments)."""
        data_dict: dict[str, Any]
        if isinstance(input_data, dict):
            data_dict = dict(input_data)
        elif hasattr(input_data, "model_dump"):
            data_dict = input_data.model_dump(mode="python", exclude_none=False)
        else:
            data_dict = dict(input_data)

        permitted_roots: list[Path] = [Path.cwd().resolve()]
        if base_dir is not None and base_dir.resolve() not in permitted_roots:
            permitted_roots.append(base_dir.resolve())

        # 1. Resolve legacy input_spec if present
        if data_dict.get("input_spec_path"):
            eff_ws = base_dir or Path.cwd().resolve()
            resolved_spec_path = resolve_workspace_path(
                str(data_dict["input_spec_path"]),
                workspace_root=eff_ws,
                allowed_roots=permitted_roots,
                allow_temp=not is_server,
            )
            if resolved_spec_path.is_file():
                try:
                    loaded = yaml.safe_load(resolved_spec_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        for k, v in loaded.items():
                            if k not in data_dict or data_dict[k] is None:
                                data_dict[k] = v
                        if base_dir is None:
                            base_dir = resolved_spec_path.parent
                except (yaml.YAMLError, ValueError, TypeError, OSError):
                    pass
        elif data_dict.get("input_spec_content"):
            try:
                loaded = yaml.safe_load(str(data_dict["input_spec_content"]))
                if isinstance(loaded, dict):
                    for k, v in loaded.items():
                        if k not in data_dict or data_dict[k] is None:
                            data_dict[k] = v
            except (yaml.YAMLError, ValueError, TypeError):
                pass
        elif isinstance(data_dict.get("input_spec"), dict):
            for k, v in data_dict["input_spec"].items():
                if k not in data_dict or data_dict[k] is None:
                    data_dict[k] = v

        def _parse_yaml_content(content: Any) -> Any:
            """Parse inline YAML or JSON content without touching the filesystem."""
            if content is None:
                return None
            if isinstance(content, (dict, BaseModel)):
                return content
            if not isinstance(content, str) or not content.strip():
                return None
            raw = content.strip()
            try:
                parsed = yaml.safe_load(raw)
                return parsed if isinstance(parsed, (dict, list)) else None
            except (yaml.YAMLError, ValueError, TypeError):
                return None

        def _load_yaml_path(p_str: Any) -> Any:
            """Load YAML/JSON from validated filesystem path (CLI only/permitted roots)."""
            if p_str is None or not isinstance(p_str, str) or not p_str.strip():
                return None
            eff_ws = base_dir or Path.cwd().resolve()
            clean_str = p_str.strip()
            resolved_p = None
            with contextlib.suppress(ValueError, OSError, RuntimeError):
                resolved_p = resolve_workspace_path(
                    clean_str,
                    workspace_root=eff_ws,
                    allowed_roots=permitted_roots,
                    allow_temp=not is_server,
                )

            if (
                (resolved_p is None or not resolved_p.is_file())
                and not Path(clean_str).is_absolute()
                and not clean_str.startswith(".")
            ):
                for root in permitted_roots:
                    matches = list(root.glob(f"**/{clean_str}"))
                    if matches and matches[0].is_file():
                        resolved_p = matches[0]
                        break

            if resolved_p is not None and resolved_p.is_file():
                try:
                    return yaml.safe_load(resolved_p.read_text(encoding="utf-8"))
                except (yaml.YAMLError, ValueError, TypeError, OSError):
                    return None

            if is_server:
                raise ValueError(
                    "Filesystem path fields (*_path) are forbidden on remote server calls."
                )
            return None

        # 2. Extract blueprint candidates (prefer inline content over path)
        resolved_bp = None
        if data_dict.get("blueprint") is not None:
            resolved_bp = _parse_yaml_content(data_dict.get("blueprint"))
        if resolved_bp is None and data_dict.get("blueprint_content") is not None:
            resolved_bp = _parse_yaml_content(data_dict.get("blueprint_content"))
        if resolved_bp is None and data_dict.get("blueprint_path") is not None:
            resolved_bp = _load_yaml_path(data_dict.get("blueprint_path"))

        # 3. Extract contract candidates (prefer inline content over path)
        resolved_contract = None
        if data_dict.get("contract") is not None:
            resolved_contract = _parse_yaml_content(data_dict.get("contract"))
        if resolved_contract is None and data_dict.get("initial_contract_content") is not None:
            resolved_contract = _parse_yaml_content(data_dict.get("initial_contract_content"))
        if resolved_contract is None and data_dict.get("initial_contract") is not None:
            resolved_contract = _parse_yaml_content(data_dict.get("initial_contract"))
        if resolved_contract is None and data_dict.get("initial_contract_path") is not None:
            resolved_contract = _load_yaml_path(data_dict.get("initial_contract_path"))
        if resolved_contract is None and resolved_bp is not None:
            if isinstance(resolved_bp, dict) and resolved_bp.get("contract"):
                resolved_contract = resolved_bp.get("contract")
            elif hasattr(resolved_bp, "contract") and resolved_bp.contract:
                resolved_contract = resolved_bp.contract
        if (
            resolved_contract is not None
            and resolved_bp is not None
            and isinstance(resolved_bp, dict)
            and not resolved_bp.get("contract")
        ):
            resolved_bp["contract"] = resolved_contract

        # 4. If blueprint was not directly provided, bundle ontology + optional contract
        if resolved_bp is None:
            ont_raw = None
            if data_dict.get("ontology") is not None:
                ont_raw = _parse_yaml_content(data_dict.get("ontology"))
            if ont_raw is None and data_dict.get("ontology_content") is not None:
                ont_raw = _parse_yaml_content(data_dict.get("ontology_content"))
            if ont_raw is None and data_dict.get("graph_a_ontology_content") is not None:
                ont_raw = _parse_yaml_content(data_dict.get("graph_a_ontology_content"))
            if ont_raw is None and data_dict.get("ontology_path") is not None:
                ont_raw = _load_yaml_path(data_dict.get("ontology_path"))
            if ont_raw is None and data_dict.get("graph_a_ontology_path") is not None:
                ont_raw = _load_yaml_path(data_dict.get("graph_a_ontology_path"))

            if ont_raw is not None:
                domain_ns = (
                    ont_raw.get("domain_namespace")
                    if isinstance(ont_raw, dict)
                    else getattr(ont_raw, "domain_namespace", None)
                ) or data_dict.get("domain_namespace") or "general"
                try:
                    resolved_bp = create_vault_blueprint(
                        domain_namespace=domain_ns,
                        ontology=ont_raw,
                        contract=resolved_contract,
                    )
                except (ValueError, TypeError, KeyError):
                    resolved_bp = {
                        "domain_namespace": domain_ns,
                        "ontology": ont_raw,
                        "contract": resolved_contract,
                    }

        # 5. Extract sources and unstructured documents (C4: no legacy wave expansion)
        sources = list(data_dict.get("sources") or [])
        documents = list(data_dict.get("unstructured_documents") or [])

        return resolved_bp, resolved_contract, sources, documents


normalize_blueprint_ingress = DualBlueprintIngressNormalizer.normalize

__all__ = [
    "DualBlueprintIngressNormalizer",
    "normalize_blueprint_ingress",
]
