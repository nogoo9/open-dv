"""Plugin registry for external OntologyRecommender modules (Decision D9, ONT-05).

Discovers, registers, and dispatches implementations of the OntologyRecommender SPI
via the entry-point group 'opendv.ontology_recommenders' (docs/18 §3E.4).
"""

from __future__ import annotations

import importlib.metadata
import inspect
import threading
from typing import Any

from opendv_contracts import (
    OntologyProposal,
    OntologyRecommender,
    OntologyRecommenderUnavailableError,
)
from opendv_contracts.dtos.ontology import OntologySuggestionRequest
from opendv_contracts.errors import ErrorCode
from opendv_service.errors import OpenDVError

ONTOLOGY_RECOMMENDER_ENTRY_POINT_GROUP = "opendv.ontology_recommenders"
ENTRYPOINT_GROUP = ONTOLOGY_RECOMMENDER_ENTRY_POINT_GROUP


class OntologyRecommenderRegistry:
    """Thread-safe discovery and management of OntologyRecommender plugins."""

    def __init__(
        self,
        entry_point_group: str = ONTOLOGY_RECOMMENDER_ENTRY_POINT_GROUP,
    ) -> None:
        self._group = entry_point_group
        self._recommenders: dict[str, Any] = {}
        self._discovered = False
        self._lock = threading.RLock()

    def _discover(self) -> None:
        with self._lock:
            if self._discovered:
                return
            self._discovered = True
            eps: Any = ()
            try:
                eps = importlib.metadata.entry_points(group=self._group)
            except (KeyError, TypeError, AttributeError, ValueError, OSError):
                eps = ()

            for ep in eps:
                try:
                    loaded = ep.load()
                    inst = loaded() if isinstance(loaded, type) else loaded
                    self.register_recommender(inst, name=ep.name)
                except (ImportError, AttributeError, TypeError, ValueError, OSError):
                    # Gracefully skip broken third-party entry points
                    pass

    def register_recommender(
        self,
        recommender: OntologyRecommender | type[OntologyRecommender] | Any,
        *,
        name: str | None = None,
    ) -> None:
        """Register an OntologyRecommender instance or class."""
        with self._lock:
            inst = recommender() if isinstance(recommender, type) else recommender

            # Protocol structural check
            has_rec = hasattr(inst, "recommender_id") or hasattr(inst, "recommend")
            if not (has_rec or callable(inst)):
                raise TypeError(
                    "Expected object conforming to OntologyRecommender protocol, "
                    f"got {type(inst).__name__}"
                )

            rec_id = str(getattr(inst, "recommender_id", name or "default")).lower().strip()
            self._recommenders[rec_id] = inst
            if name:
                self._recommenders[name.lower().strip()] = inst

    register = register_recommender

    def unregister_recommender(self, recommender_id: str) -> None:
        """Unregister an in-memory recommender."""
        with self._lock:
            self._recommenders.pop(recommender_id.lower().strip(), None)

    def has_recommender(self, recommender_id: str | None = None) -> bool:
        """Check whether any recommender or a specific recommender ID is registered."""
        self._discover()
        with self._lock:
            if recommender_id is None:
                return len(self._recommenders) > 0
            return recommender_id.lower().strip() in self._recommenders

    def get_recommender(
        self,
        recommender_id: str | None = None,
    ) -> Any:
        """Retrieve a registered OntologyRecommender.

        If recommender_id is None, returns the single registered recommender (or the 'default').
        Raises OntologyRecommenderUnavailableError if unavailable.
        """
        self._discover()
        with self._lock:
            if not self._recommenders:
                raise OntologyRecommenderUnavailableError(
                    "No OntologyRecommender module is registered under "
                    f"'{self._group}'. Please install an ontology recommender plugin "
                    "(e.g. opendv-testkit or an enterprise recommender package) "
                    "or set ontology policy to 'off'."
                )

            if recommender_id is None:
                if "default" in self._recommenders:
                    return self._recommenders["default"]
                return next(iter(self._recommenders.values()))

            key = recommender_id.lower().strip()
            if key not in self._recommenders:
                available = sorted(self._recommenders.keys())
                raise OntologyRecommenderUnavailableError(
                    f"OntologyRecommender '{recommender_id}' is not installed. "
                    f"Available recommenders: {available}."
                )
            return self._recommenders[key]

    def list_recommenders(self) -> list[str]:
        """List all registered recommender identifiers."""
        self._discover()
        with self._lock:
            return sorted(self._recommenders.keys())

    def reset(self) -> None:
        """Reset the registry to an empty, undiscovered state (for testing)."""
        with self._lock:
            self._recommenders.clear()
            self._discovered = False


_DEFAULT_REGISTRY: OntologyRecommenderRegistry | None = None
_INIT_LOCK = threading.Lock()


def get_ontology_recommender_registry() -> OntologyRecommenderRegistry:
    """Obtain the global default OntologyRecommenderRegistry singleton."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        with _INIT_LOCK:
            if _DEFAULT_REGISTRY is None:
                _DEFAULT_REGISTRY = OntologyRecommenderRegistry()
    return _DEFAULT_REGISTRY


def set_default_ontology_recommender_registry(
    registry: OntologyRecommenderRegistry | None,
) -> None:
    """Override or clear the default global registry (for testing)."""
    global _DEFAULT_REGISTRY
    with _INIT_LOCK:
        _DEFAULT_REGISTRY = registry


def register_recommender(recommender: Any, recommender_id: str | None = None) -> None:
    """Register an in-memory recommender instance (used by testkit and plugins)."""
    reg = get_ontology_recommender_registry()
    if (
        isinstance(recommender, str)
        and recommender_id is not None
        and not isinstance(recommender_id, str)
    ):
        reg.register_recommender(recommender_id, name=recommender)
    else:
        reg.register_recommender(recommender, name=recommender_id)


def unregister_recommender(recommender_id: str) -> None:
    """Unregister an in-memory recommender."""
    get_ontology_recommender_registry().unregister_recommender(recommender_id)


def clear_recommenders() -> None:
    """Clear in-memory registered recommenders."""
    get_ontology_recommender_registry().reset()


def list_registered_recommenders() -> list[str]:
    """Return list of all registered recommender identifiers."""
    return get_ontology_recommender_registry().list_recommenders()


def get_ontology_recommender(recommender_id: str | None = None) -> Any:
    """Discover and return an ontology recommender by ID or the default installed one."""
    return get_ontology_recommender_registry().get_recommender(recommender_id)


async def invoke_recommender(
    request: OntologySuggestionRequest,
    recommender_id: str | None = None,
) -> OntologyProposal:
    """Invoke the active ontology recommender with request context."""
    recommender = get_ontology_recommender(recommender_id)
    recommend_fn = getattr(recommender, "recommend", None)
    if not callable(recommend_fn) and callable(recommender):
        recommend_fn = recommender
    if not callable(recommend_fn):
        raise OpenDVError(
            f"Installed recommender '{recommender}' does not implement recommend() method.",
            error_code=ErrorCode.INTERNAL_ERROR,
        )

    if inspect.iscoroutinefunction(recommend_fn):
        result = await recommend_fn(request)
    else:
        result = recommend_fn(request)

    if isinstance(result, OntologyProposal):
        return result
    if isinstance(result, dict):
        return OntologyProposal.model_validate(result)

    raise OpenDVError(
        f"Recommender returned invalid response type: {type(result)}",
        error_code=ErrorCode.INTERNAL_ERROR,
    )


__all__ = [
    "ENTRYPOINT_GROUP",
    "ONTOLOGY_RECOMMENDER_ENTRY_POINT_GROUP",
    "OntologyRecommenderRegistry",
    "clear_recommenders",
    "get_ontology_recommender",
    "get_ontology_recommender_registry",
    "invoke_recommender",
    "list_registered_recommenders",
    "register_recommender",
    "set_default_ontology_recommender_registry",
    "unregister_recommender",
]
