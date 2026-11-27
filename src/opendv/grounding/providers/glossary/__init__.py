"""YAML glossary provider package (Layer-2a)."""

from __future__ import annotations

from opendv.grounding.providers.glossary.knowledge_source import GlossaryKnowledgeSource
from opendv.grounding.providers.glossary.loader import (
    MAX_GLOSSARY_BYTES,
    GlossaryDocument,
    GlossaryExpectedType,
    GlossaryKind,
    GlossaryTermSpec,
    LoadedGlossary,
    get_example_glossary_path,
    load_glossary,
    validate_safe_regex,
)

__all__ = [
    "MAX_GLOSSARY_BYTES",
    "GlossaryDocument",
    "GlossaryExpectedType",
    "GlossaryKind",
    "GlossaryKnowledgeSource",
    "GlossaryTermSpec",
    "LoadedGlossary",
    "get_example_glossary_path",
    "load_glossary",
    "validate_safe_regex",
]
