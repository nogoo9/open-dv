"""Construction agent SPI protocol for LLM reasoning harnesses and personas."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from opendv_contracts.dtos.ontology import OntologyDelta, OntologyProposal


@runtime_checkable
class ConstructionAgent(Protocol):
    """SPI protocol for LLM reasoning personas and ontology construction agents."""

    def propose_delta(
        self,
        base_ontology: Any,
        evidence: list[Any] | None = None,
        **kwargs: Any,
    ) -> OntologyDelta | OntologyProposal:
        """Generate candidate ontology delta given base ontology and empirical evidence."""
        ...

    def clarify(
        self,
        context: Any,
        **kwargs: Any,
    ) -> Any:
        """Generate clarification questions or evaluate user responses."""
        ...
