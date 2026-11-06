"""Ontology seam and recommender API routes (`/api/v1/ontology`).

Supports proposal suggestion, delta validation, structural diffing,
and steward decision recording (IP-1, IP-6, Phase P7).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from opendv_contracts import (
    OntologyDecisionRequest,
    OntologyDecisionResponse,
    OntologyDiff,
    OntologyDiffRequest,
    OntologyProposal,
    OntologySuggestionRequest,
    OntologyValidateDeltaRequest,
    OntologyValidationResult,
)
from opendv_platform_api.deps import get_opendv_service
from opendv_service import OpenDVService

router = APIRouter(prefix="/ontology", tags=["ontology"])


@router.post("/suggestions", response_model=OntologyProposal)
async def suggest_ontology(
    req: OntologySuggestionRequest,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> OntologyProposal:
    """Suggest ontology extensions and mappings from candidate sources."""
    return await service.suggest_ontology_async(req)


@router.post("/validate-delta", response_model=OntologyValidationResult)
async def validate_ontology_delta(
    req: OntologyValidateDeltaRequest,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> OntologyValidationResult:
    """Validate an ontology delta against Graph A evolution locks."""
    return service.validate_ontology_delta(req)


@router.post("/diff", response_model=OntologyDiff)
async def diff_ontology(
    req: OntologyDiffRequest,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> OntologyDiff:
    """Compute structural difference between two Graph A ontologies."""
    return service.diff_ontology(req)


@router.post("/decisions", response_model=OntologyDecisionResponse)
async def record_ontology_decision(
    req: OntologyDecisionRequest,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> OntologyDecisionResponse:
    """Record steward decision on an ontology proposal and apply delta if accepted."""
    return service.decide_ontology(req)


__all__ = ["router"]
