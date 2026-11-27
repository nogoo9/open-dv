"""FIBO Pack v2 loader, taxonomic/role closure, acronyms, and Q&A adapter."""

from __future__ import annotations

from opendv.grounding.providers.fibo.acronyms import (
    FINANCIAL_ACRONYMS,
    SEC_CONTEXT_TRIGGERS,
    SEC_EXCLUSION_TOKENS,
    expand_financial_tokens,
    should_expand_sec,
)
from opendv.grounding.providers.fibo.closure import (
    PackDerivedIndexes,
    SubclassClosureResolver,
    build_pack_v2_derived_indexes,
    minimal_taxonomic_leaves,
    resolve_subclass_closure,
)
from opendv.grounding.providers.fibo.closure_rules import (
    BROAD_MATCH_PREDICATE,
    DEFAULT_MAX_DEPTH,
    DEFAULT_PROVENANCE,
    DEFAULT_SCHEME,
    GROUNDING_THRESHOLD,
    SubclassClosureMatch,
    TaxonomyCycleError,
    UngroundedSeedMappingError,
    can_satisfy_path_a_grounding,
    normalize_score,
    validate_closure_grounding_eligibility,
)
from opendv.grounding.providers.fibo.knowledge_source import FiboKnowledgeSource
from opendv.grounding.providers.fibo.loader import (
    FIBO_V2_MODULES,
    MAX_DISK_SIZE_BYTES,
    MAX_LOAD_LATENCY_MS,
    MAX_MEMORY_BYTES,
    PACK_V2_SCHEMA_VERSION,
    FiboPackQueryAdapter,
    FiboReferenceOntologyLoader,
    ReferenceOntologyCorruptError,
    clear_fibo_loader_cache,
    get_bundled_fibo_pack_sha256,
    get_default_fibo_loader,
)

__all__ = [
    "BROAD_MATCH_PREDICATE",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_PROVENANCE",
    "DEFAULT_SCHEME",
    "FIBO_V2_MODULES",
    "FINANCIAL_ACRONYMS",
    "GROUNDING_THRESHOLD",
    "MAX_DISK_SIZE_BYTES",
    "MAX_LOAD_LATENCY_MS",
    "MAX_MEMORY_BYTES",
    "PACK_V2_SCHEMA_VERSION",
    "SEC_CONTEXT_TRIGGERS",
    "SEC_EXCLUSION_TOKENS",
    "FiboKnowledgeSource",
    "FiboPackQueryAdapter",
    "FiboReferenceOntologyLoader",
    "PackDerivedIndexes",
    "ReferenceOntologyCorruptError",
    "SubclassClosureMatch",
    "SubclassClosureResolver",
    "TaxonomyCycleError",
    "UngroundedSeedMappingError",
    "build_pack_v2_derived_indexes",
    "can_satisfy_path_a_grounding",
    "clear_fibo_loader_cache",
    "expand_financial_tokens",
    "get_bundled_fibo_pack_sha256",
    "get_default_fibo_loader",
    "minimal_taxonomic_leaves",
    "normalize_score",
    "resolve_subclass_closure",
    "should_expand_sec",
    "validate_closure_grounding_eligibility",
]
