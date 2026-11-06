"""Entry-point plugin registry and discovery for external OntologyRecommender modules.

References docs/18 §3E.4.
"""

from __future__ import annotations

import importlib.metadata
import inspect
from typing import Any

from opendv_contracts.dtos.ontology import (
    OntologyProposal,
    OntologySuggestionRequest,
)
from opendv_contracts.errors import ErrorCode
from opendv_service.errors import OpenDVError

ENTRYPOINT_GROUP = "opendv.ontology_recommenders"

# In-memory registry for programmatic / testkit registration
_MANUAL_REGISTRY: dict[str, Any] = {}


def register_recommender(recommender: Any, recommender_id: str | None = None) -> None:
    """Register an in-memory recommender instance (used by opendv-testkit and plugins)."""
    if (
        isinstance(recommender, str)
        and recommender_id is not None
        and not isinstance(recommender_id, str)
    ):
        rec_id = recommender
        rec_obj = recommender_id
    else:
        rec_id = recommender_id or getattr(recommender, "recommender_id", "default")
        rec_obj = recommender
    _MANUAL_REGISTRY[rec_id] = rec_obj


def unregister_recommender(recommender_id: str) -> None:
    """Unregister an in-memory recommender."""
    _MANUAL_REGISTRY.pop(recommender_id, None)


def clear_recommenders() -> None:
    """Clear in-memory registered recommenders."""
    _MANUAL_REGISTRY.clear()


def list_registered_recommenders() -> list[str]:
    """Return list of all registered recommender identifiers."""
    ids = list(_MANUAL_REGISTRY.keys())
    try:
        eps = importlib.metadata.entry_points(group=ENTRYPOINT_GROUP)
        for ep in eps:
            if ep.name not in ids:
                ids.append(ep.name)
    except (TypeError, KeyError, AttributeError, OSError):
        pass
    return ids


def get_ontology_recommender(recommender_id: str | None = None) -> Any:
    """Discover and return an ontology recommender by ID or the default installed one.

    Checks:
    1. In-memory manual registry (_MANUAL_REGISTRY).
    2. Python entry points under group 'opendv.ontology_recommenders'.

    Raises:
        OpenDVError(error_code=ErrorCode.ONTOLOGY_RECOMMENDER_UNAVAILABLE) if no
        recommender is available.
    """
    if recommender_id and recommender_id in _MANUAL_REGISTRY:
        return _MANUAL_REGISTRY[recommender_id]
    if not recommender_id and _MANUAL_REGISTRY:
        return next(iter(_MANUAL_REGISTRY.values()))

    # Discover via entry points
    try:
        eps = importlib.metadata.entry_points(group=ENTRYPOINT_GROUP)
        for ep in eps:
            if recommender_id is None or ep.name == recommender_id:
                plugin_cls = ep.load()
                inst = plugin_cls() if callable(plugin_cls) else plugin_cls
                return inst
    except (TypeError, KeyError, AttributeError, ImportError, OSError):
        pass

    target_name = recommender_id or "default"
    raise OpenDVError(
        f"No ontology recommender module is installed or registered "
        f"(requested='{target_name}'). "
        f"Install an external recommender package extending '{ENTRYPOINT_GROUP}' "
        f"or register a testkit recommender.",
        error_code=ErrorCode.ONTOLOGY_RECOMMENDER_UNAVAILABLE,
    )


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
    "clear_recommenders",
    "get_ontology_recommender",
    "invoke_recommender",
    "list_registered_recommenders",
    "register_recommender",
    "unregister_recommender",
]
