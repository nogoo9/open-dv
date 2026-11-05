"""Minimal schema instance synthesis and default generators for test doubles.

Conforms to docs/03-verification-and-agentic-architecture.md
and docs/09-agnostic-agent-harness-and-sdk-spec.md.
"""

from __future__ import annotations

import enum
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel

_PIPELINE_STAGE_SCHEMA_NAMES = frozenset(
    {
        "Stage00OrchestrationPlanOutput",
        "Stage01SourceDiscoveryOutput",
        "Stage02UnstructuredDiscoveryOutput",
        "Stage03ColumnTriageOutput",
        "Stage04ContractSynthesisOutput",
        "NoMistakeDataVault2ContractV5",
    }
)


def validate_candidate_with_schema(schema_cls: type[BaseModel], candidate: Any) -> Any:
    """Validate `candidate` (model instance, dict, or JSON string) against `schema_cls`."""
    if isinstance(candidate, schema_cls):
        return candidate
    if isinstance(candidate, BaseModel):
        return schema_cls.model_validate(candidate.model_dump(mode="json"))
    if isinstance(candidate, dict):
        return schema_cls.model_validate(candidate)
    if isinstance(candidate, str):
        return schema_cls.model_validate_json(candidate)
    return candidate


def default_for_annotation(annotation: Any, *, non_empty_collections: bool = False) -> Any:
    """Construct a minimal valid default value for a type annotation."""
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Literal and args:
        return args[0]
    if origin in (list, tuple, set, frozenset):
        if non_empty_collections and args:
            elem = default_for_annotation(args[0], non_empty_collections=True)
            return [elem] if elem is not None else []
        return []
    if origin is dict:
        return {}
    if origin is not None and args:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) < len(args) and not non_empty_collections:
            return None
        if non_none:
            return default_for_annotation(
                non_none[0],
                non_empty_collections=non_empty_collections,
            )

    if isinstance(annotation, type):
        if issubclass(annotation, enum.Enum):
            members = list(annotation)
            return members[0].value if members else "default"
        if issubclass(annotation, BaseModel):
            inst = synthesize_minimal_schema_instance(annotation)
            return inst.model_dump(mode="json") if inst is not None else {}
        if issubclass(annotation, bool):
            return True
        if issubclass(annotation, int):
            return 1
        if issubclass(annotation, float):
            return 1.0
        if issubclass(annotation, str):
            return "deterministic"
    return "deterministic"


def synthesize_minimal_schema_instance(
    schema_cls: type[BaseModel],
    prompt: str = "",
) -> Any:
    """Synthesize a valid instance of `schema_cls` from `prompt` context or field defaults."""
    if schema_cls.__name__ in _PIPELINE_STAGE_SCHEMA_NAMES:
        try:
            from opendv_agent_harness.pipeline import synthesize_stage_output_from_prompt

            stage_out = synthesize_stage_output_from_prompt(schema_cls, prompt)
            if stage_out is not None:
                return stage_out
        except Exception:
            pass

    try:
        return schema_cls.model_validate({})
    except Exception:
        pass

    for non_empty in (False, True):
        payload: dict[str, Any] = {}
        for field_name, field_info in schema_cls.model_fields.items():
            if not field_info.is_required() and not non_empty:
                continue
            payload[field_name] = default_for_annotation(
                field_info.annotation,
                non_empty_collections=non_empty,
            )
        try:
            return schema_cls.model_validate(payload)
        except Exception:
            continue

    return None


_synthesize_minimal_schema_instance = synthesize_minimal_schema_instance


def resolve_schema_class(name: str) -> type[BaseModel] | None:
    """Resolve a BaseModel subclass by class name from known Open-DV modules."""
    for mod_name in ("opendv_agent_harness.pipeline", "opendv_contracts", "opendv_core"):
        try:
            mod = __import__(mod_name, fromlist=[name])
            candidate = getattr(mod, name, None)
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                return candidate
        except Exception:
            continue
    return None
