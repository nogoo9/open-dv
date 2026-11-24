"""Static JSON FIBO reference pack loader and subclass closure resolver."""

from opendv.sources.fibo.closure import (
    SubclassClosureResolver,
    resolve_subclass_closure,
)
from opendv.sources.fibo.closure_rules import (
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
from opendv.sources.fibo.loader import (
    CANONICAL_FIBO_CORE_PREFIX,
    CANONICAL_FIBO_CORE_SHA256,
    CANONICAL_FIBO_CORE_UPSTREAM_COMMIT,
    CANONICAL_FIBO_CORE_UPSTREAM_TAG,
    MAX_DISK_SIZE_BYTES,
    MAX_LOAD_LATENCY_MS,
    MAX_MEMORY_BYTES,
    FiboReferenceOntologyLoader,
    ReferenceOntologyCorruptError,
    clear_fibo_loader_cache,
    get_default_fibo_loader,
)

__all__ = [
    "BROAD_MATCH_PREDICATE",
    "CANONICAL_FIBO_CORE_PREFIX",
    "CANONICAL_FIBO_CORE_SHA256",
    "CANONICAL_FIBO_CORE_UPSTREAM_COMMIT",
    "CANONICAL_FIBO_CORE_UPSTREAM_TAG",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_PROVENANCE",
    "DEFAULT_SCHEME",
    "GROUNDING_THRESHOLD",
    "MAX_DISK_SIZE_BYTES",
    "MAX_LOAD_LATENCY_MS",
    "MAX_MEMORY_BYTES",
    "FiboReferenceOntologyLoader",
    "ReferenceOntologyCorruptError",
    "SubclassClosureMatch",
    "SubclassClosureResolver",
    "TaxonomyCycleError",
    "UngroundedSeedMappingError",
    "can_satisfy_path_a_grounding",
    "clear_fibo_loader_cache",
    "get_default_fibo_loader",
    "normalize_score",
    "resolve_subclass_closure",
    "validate_closure_grounding_eligibility",
]
