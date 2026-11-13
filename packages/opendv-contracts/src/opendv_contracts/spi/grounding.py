"""Grounding verification SPI protocols and result DTOs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel


class GroundingVerificationResult(BaseContractModel):
    """Result report emitted by a GroundingVerifier."""

    is_grounded: bool = Field(
        ..., description="Whether concept satisfied reference grounding threshold"
    )
    score: float = Field(..., ge=0.0, le=1.0, description="Quantized match confidence score")
    matched_uri: str | None = Field(
        default=None, description="Matched standard reference concept URI"
    )
    matched_label: str | None = Field(default=None, description="Matched reference concept label")
    diagnostics: list[str] = Field(default_factory=list, description="Advisory diagnostic messages")


@runtime_checkable
class GroundingVerifier(Protocol):
    """SPI protocol for grounding verification against reference ontologies (ONT-EVO-002)."""

    def verify_grounding(
        self,
        concept_uri: str,
        candidates: list[Any],
        concept_name: str = "",
        synonyms: Sequence[str] = (),
        **kwargs: Any,
    ) -> GroundingVerificationResult:
        """Verify whether concept_uri satisfies grounding threshold against reference candidates."""
        ...
