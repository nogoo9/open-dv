"""Policy engine for Ontology Recommendation governance (Decision D9, ONT-05).

Enforces REQ-ONT-02 and REQ-FAIL-01:
- 'off' (default): extension hooks are never invoked.
- 'suggest_only': IP-1, IP-2, IP-4 produce advisory proposals; Gate 1 hold remains plain.
- 'steward_review': IP-3 populates typed hold options with proposed delta ops.
- 'auto_accept_above': proposals with confidence >= threshold are eligible for automated acceptance.
- Fail-fast validation: active policy without an installed recommender raises loud
  ONTOLOGY_RECOMMENDER_UNAVAILABLE (exit code 2 / HTTP 422).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from opendv_contracts import (
    InputValidationError,
    OntologyProposal,
    OntologyRecommenderUnavailableError,
)
from opendv_service.ontology.registry import (
    OntologyRecommenderRegistry,
    get_ontology_recommender_registry,
)

OntologyPolicyMode = Literal[
    "off",
    "suggest_only",
    "steward_review",
    "auto_accept_above",
]


class OntologyPolicy(BaseModel):
    """Configuration and decision logic governing ontology recommendations."""

    model_config = ConfigDict(extra="forbid")

    mode: OntologyPolicyMode = Field(
        default="off",
        description="Policy mode: off, suggest_only, steward_review, auto_accept_above",
    )
    auto_accept_threshold: float = Field(
        default=0.9,
        ge=0.0,
        le=1.0,
        description="Confidence threshold for auto_accept_above mode",
    )
    recommender_id: str | None = Field(
        default=None,
        description="Optional explicit recommender plugin ID",
    )
    clearance_level: str = Field(
        default="INTERNAL",
        description="RBAC clearance level for input sample data redaction",
    )

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, val: Any) -> str:
        if val is None:
            return "off"
        raw = str(val).strip().lower()
        if raw in {"", "disabled", "false", "none"}:
            return "off"
        if raw.startswith("auto_accept_above"):
            return "auto_accept_above"
        if raw not in {"off", "suggest_only", "steward_review", "auto_accept_above"}:
            raise InputValidationError(
                f"Invalid ontology recommendation policy '{val}'. "
                "Must be one of: 'off', 'suggest_only', 'steward_review', "
                "'auto_accept_above[:<threshold>]'."
            )
        return raw

    @classmethod
    def from_value(cls, val: Any) -> OntologyPolicy:
        """Parse an OntologyPolicy from a string, dict, or existing instance."""
        if isinstance(val, OntologyPolicy):
            return val
        if val is None or val == "":
            return cls(mode="off")
        if isinstance(val, dict):
            # Support dict with policy, mode, or threshold keys
            d = dict(val)
            mode_raw = d.pop("policy", None) or d.pop("mode", "off")
            return cls.from_string(str(mode_raw), **d)
        if isinstance(val, str):
            return cls.from_string(val)
        raise InputValidationError(
            f"Cannot parse OntologyPolicy from value of type {type(val).__name__}"
        )

    @classmethod
    def from_string(cls, raw: str, **kwargs: Any) -> OntologyPolicy:
        """Parse policy string such as 'off', 'suggest_only', or 'auto_accept_above:0.85'."""
        cleaned = raw.strip().lower()
        if ":" in cleaned and cleaned.startswith("auto_accept_above"):
            parts = cleaned.split(":", 1)
            try:
                threshold = float(parts[1].strip())
            except ValueError as exc:
                raise InputValidationError(
                    f"Invalid threshold '{parts[1]}' in ontology policy '{raw}'. "
                    "Must be a float between 0.0 and 1.0."
                ) from exc
            return cls(mode="auto_accept_above", auto_accept_threshold=threshold, **kwargs)

        return cls(mode=cls._normalize_mode(cleaned), **kwargs)

    @property
    def is_active(self) -> bool:
        """Return True if recommendations are enabled (any mode other than 'off')."""
        return self.mode != "off"

    def should_invoke_ip1(self) -> bool:
        """IP-1 bootstrap suggestion is invokable directly or when policy is active."""
        return True

    def should_invoke_ip2(self) -> bool:
        """IP-2 advisory post-profiling fires if policy is active."""
        return self.is_active

    def should_invoke_ip3(self) -> bool:
        """IP-3 blocking hold fires if policy is active."""
        return self.is_active

    def should_populate_hold_options(self) -> bool:
        """Whether proposed delta options are attached to StewardHoldRecord."""
        return self.mode in {"steward_review", "auto_accept_above"}

    def should_invoke_ip4(self) -> bool:
        """IP-4 advisory unresolved mentions fires if policy is active."""
        return self.is_active

    def should_auto_accept(self, proposal: OntologyProposal) -> bool:
        """Determine if a proposal meets criteria for automated acceptance."""
        if self.mode != "auto_accept_above":
            return False
        return proposal.confidence_score >= self.auto_accept_threshold

    def validate_availability(
        self,
        registry: OntologyRecommenderRegistry | None = None,
    ) -> None:
        """Fail loudly with ONTOLOGY_RECOMMENDER_UNAVAILABLE if policy is
        enabled without a module.
        """
        if not self.is_active:
            return

        reg = registry or get_ontology_recommender_registry()
        if not reg.has_recommender(self.recommender_id):
            target = f" '{self.recommender_id}'" if self.recommender_id else ""
            available = reg.list_recommenders()
            raise OntologyRecommenderUnavailableError(
                f"Ontology recommendation policy is active ('{self.mode}'), but no "
                f"OntologyRecommender{target} is available under 'opendv.ontology_recommenders'. "
                f"Installed recommenders: {available}. "
                "Please install a recommender plugin (e.g. opendv-testkit or an enterprise "
                "recommender package) or set ontology policy to 'off'."
            )


__all__ = [
    "OntologyPolicy",
    "OntologyPolicyMode",
]
