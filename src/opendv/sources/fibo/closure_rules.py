"""Grounding eligibility rules, exceptions, and match DTOs for FIBO subclass closure."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.ontology import ExternalMapping

DEFAULT_MAX_DEPTH: Final[int] = 32
BROAD_MATCH_PREDICATE: Final[str] = "skos:broadMatch"
DEFAULT_PROVENANCE: Final[str] = "deterministic"
DEFAULT_SCHEME: Final[str] = "fibo"
GROUNDING_THRESHOLD: Final[Decimal] = Decimal("0.6500")
_DECIMAL_FOUR_PLACES: Final[Decimal] = Decimal("0.0001")


class UngroundedSeedMappingError(OpenDVError, ValueError):
    """Raised when transitive closure is attempted on an ungrounded seed (< 0.6500)."""

    error_code: ErrorCode = ErrorCode.ONT_EVO_CONTRACT_VIOLATION

    def __init__(
        self,
        message: str,
        *,
        concept_uri: str | None = None,
        direct_seed_score: float | Decimal | None = None,
        threshold: float | Decimal = GROUNDING_THRESHOLD,
    ) -> None:
        super().__init__(message, error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION)
        self.concept_uri = concept_uri
        self.direct_seed_score = float(direct_seed_score) if direct_seed_score is not None else None
        self.threshold = float(threshold)


class TaxonomyCycleError(OpenDVError, ValueError):
    """Raised when a circular inheritance relationship is detected in taxonomic hierarchy."""

    error_code: ErrorCode = ErrorCode.SYNTAX_INVALID_ARGUMENT

    def __init__(
        self,
        message: str,
        *,
        cycle_path: list[str] | None = None,
    ) -> None:
        super().__init__(message, error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT)
        self.cycle_path = list(cycle_path or [])


class SubclassClosureMatch(BaseModel):
    """Inferred taxonomic superclass resolved via transitive subClassOf closure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    uri: str = Field(..., description="Target reference ontology superclass URI")
    label: str | None = Field(default=None, description="Concept human-readable label")
    depth: int = Field(..., ge=1, description="Taxonomic distance from direct seed concept")
    predicate: str = Field(
        default=BROAD_MATCH_PREDICATE,
        description="Taxonomic relationship predicate ('skos:broadMatch')",
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="Inherited seed score")
    provenance: str = Field(default=DEFAULT_PROVENANCE)
    reference_pack_id: str | None = Field(default=None)
    reference_pack_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    path: tuple[str, ...] = Field(default_factory=tuple)
    definition: str | None = Field(default=None)

    @property
    def satisfies_path_a(self) -> bool:
        """Transitive skos:broadMatch superclasses cannot satisfy Path A alone."""
        return False

    @property
    def is_standalone_grounding_eligible(self) -> bool:
        """Transitive skos:broadMatch cannot satisfy standalone positive grounding."""
        return False

    def to_external_mapping(
        self,
        *,
        mapping_type: Literal["skos:exactMatch", "skos:closeMatch"] = "skos:closeMatch",
    ) -> ExternalMapping:
        """Convert to a V2 ExternalMapping (reconciling skos:broadMatch to close/exactMatch)."""
        if mapping_type not in ("skos:exactMatch", "skos:closeMatch"):
            raise OpenDVError(
                f"V2 ExternalMapping.mapping_type forbids '{mapping_type}'",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        return ExternalMapping(
            standard_uri=self.uri,
            reference_pack_id=self.reference_pack_id or DEFAULT_SCHEME,
            reference_pack_sha256=self.reference_pack_sha256 or ("0" * 64),
            mapping_type=mapping_type,
            confidence=Decimal(str(self.confidence)).quantize(
                _DECIMAL_FOUR_PLACES, rounding=ROUND_HALF_EVEN
            ),
        )


def normalize_score(score: float | Decimal | str | int) -> Decimal:
    """Normalize and quantize confidence score to 4 decimal places."""
    if isinstance(score, Decimal):
        dec = score
    elif isinstance(score, (float, int, str)):
        try:
            dec = Decimal(str(score))
        except Exception as err:
            raise ValueError(f"Invalid score representation '{score}': {err}") from err
    else:
        raise TypeError(f"Unsupported score type: {type(score).__name__}")

    if dec < Decimal("0.0") or dec > Decimal("1.0"):
        raise ValueError(f"Confidence score must be between 0.0 and 1.0, got {dec}")
    return dec.quantize(_DECIMAL_FOUR_PLACES, rounding=ROUND_HALF_EVEN)


def can_satisfy_path_a_grounding(predicate: str | None) -> bool:
    """Only direct 'skos:exactMatch' and 'skos:closeMatch' satisfy Path A."""
    if predicate is None:
        return False
    return predicate.strip().lower() in (
        "skos:exactmatch",
        "exactmatch",
        "skos:closematch",
        "closematch",
    )


def validate_closure_grounding_eligibility(matches: Sequence[SubclassClosureMatch]) -> None:
    """Assert transitive subclass closure matches cannot satisfy standalone Path A."""
    for match in matches:
        if match.predicate != BROAD_MATCH_PREDICATE:
            raise ValueError(
                f"Invalid closure predicate '{match.predicate}': must be '{BROAD_MATCH_PREDICATE}'"
            )
        if match.satisfies_path_a or match.is_standalone_grounding_eligible:
            raise ValueError("Transitive closure matches cannot serve as standalone grounding")


__all__ = [
    "BROAD_MATCH_PREDICATE",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_PROVENANCE",
    "DEFAULT_SCHEME",
    "GROUNDING_THRESHOLD",
    "SubclassClosureMatch",
    "TaxonomyCycleError",
    "UngroundedSeedMappingError",
    "can_satisfy_path_a_grounding",
    "normalize_score",
    "validate_closure_grounding_eligibility",
]
