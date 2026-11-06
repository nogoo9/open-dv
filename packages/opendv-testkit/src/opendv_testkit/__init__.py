"""Open-DV Dev-Only Testkit.

Provides deterministic harness adapters, in-memory vendor SDK shims, minimal schema
synthesis, pipeline runner test seams, and canonical test fixtures.
"""

from __future__ import annotations

from opendv_testkit.fixtures import (
    PE_DEMO_CONTEXT_REFS,
    WORKBENCH_VIEW_TITLES,
    build_pe_canonical_events,
    create_ephemeral_project,
    create_ephemeral_run,
    seed_canonical_pe_demo,
)
from opendv_testkit.harness_adapter import (
    DEFAULT_DETERMINISTIC_MODEL_MAP,
    DeterministicHarnessAdapter,
)
from opendv_testkit.ontology import DeterministicTestkitRecommender
from opendv_testkit.runner import (
    build_deterministic_pipeline_runner,
    deterministic_pipeline_seam,
    make_deterministic_pipeline_runner,
)
from opendv_testkit.shims import (
    build_deterministic_agy_sdk_shim,
    build_deterministic_claude_sdk_shim,
)
from opendv_testkit.sources import RecordsSourceAdapter
from opendv_testkit.synthesis import (
    default_for_annotation,
    resolve_schema_class,
    synthesize_minimal_schema_instance,
    validate_candidate_with_schema,
)

__all__ = [
    "DEFAULT_DETERMINISTIC_MODEL_MAP",
    "PE_DEMO_CONTEXT_REFS",
    "WORKBENCH_VIEW_TITLES",
    "DeterministicHarnessAdapter",
    "DeterministicTestkitRecommender",
    "RecordsSourceAdapter",
    "build_deterministic_agy_sdk_shim",
    "build_deterministic_claude_sdk_shim",
    "build_deterministic_pipeline_runner",
    "build_pe_canonical_events",
    "create_ephemeral_project",
    "create_ephemeral_run",
    "default_for_annotation",
    "deterministic_pipeline_seam",
    "make_deterministic_pipeline_runner",
    "resolve_schema_class",
    "seed_canonical_pe_demo",
    "synthesize_minimal_schema_instance",
    "validate_candidate_with_schema",
]
