"""Canonical 8-Stage Pipeline Explorer templates, phases, and default fixtures.

Conforms to docs/design/open-dv-workbench/HANDOFF.md, Decision D3, and Directive R9.
"""

from __future__ import annotations

from opendv_contracts.dtos.explorer import (
    PhaseSpecDTO,
    StageDTO,
    WaveMetadataSpecDTO,
)
from opendv_service.use_cases.explorer_canonical_stages_part1 import (
    build_stages_00_to_03,
)
from opendv_service.use_cases.explorer_canonical_stages_part2 import (
    build_stages_04_to_07,
)
from opendv_service.use_cases.explorer_canonical_viz import (
    build_canonical_doc_viz,
    build_canonical_dv_viz,
    build_canonical_ontology_viz,
)

__all__ = [
    "DEFAULT_WAVES",
    "EXPLORER_PHASES",
    "build_canonical_doc_viz",
    "build_canonical_dv_viz",
    "build_canonical_ontology_viz",
    "build_canonical_stages",
]

EXPLORER_PHASES: list[PhaseSpecDTO] = [
    PhaseSpecDTO(index=0, label="Discovery", sub="LLM agents · read-only scouts"),
    PhaseSpecDTO(index=1, label="Modelling", sub="LLM agent · declarative contract only"),
    PhaseSpecDTO(index=2, label="Verification", sub="deterministic · symbolic gates"),
    PhaseSpecDTO(index=3, label="Compilation", sub="deterministic · no LLM"),
]

DEFAULT_WAVES: list[WaveMetadataSpecDTO] = [
    WaveMetadataSpecDTO(
        id="W0", name="wave_0_cold_start_crm_erp", description="Cold Start (CRM + ERP)"
    ),
    WaveMetadataSpecDTO(
        id="W1", name="wave_1_incremental_stripe", description="Incremental Stripe Invoices"
    ),
    WaveMetadataSpecDTO(
        id="W2", name="wave_2_crm_schema_drift", description="Intra-Source Schema Drift"
    ),
    WaveMetadataSpecDTO(
        id="W3", name="wave_3_unstructured_msa", description="Unstructured MSA Extraction"
    ),
]


def build_canonical_stages() -> list[StageDTO]:
    """Construct the strictly ordered 8 canonical stages (STAGE_00..STAGE_07)."""
    return build_stages_00_to_03() + build_stages_04_to_07()
