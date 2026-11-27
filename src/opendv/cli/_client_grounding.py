"""Thin client transport helpers for `ontology ground` and `ontology fibo-sync`."""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any, Final, Protocol, cast

from opendv.models import (
    ErrorCode,
    FiboSyncReq,
    FiboSyncResp,
    GroundingResponse,
    OntologyGroundReq,
    OpenDVError,
)

if TYPE_CHECKING:
    import httpx

    from opendv.service import OpenDVService

_MIN_GROUNDING_TIMEOUT_SECONDS: Final[float] = 900.0


class GroundingClientPort(Protocol):
    """Minimal transport port satisfied by CliClient / CliWorkflowMixin."""

    @property
    def is_remote(self) -> bool:
        """Whether the CLI targets a remote HTTP API server."""
        ...

    def service(self) -> OpenDVService:
        """Return the in-process OpenDVService."""
        ...

    def _http(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
    ) -> httpx.Response:
        """Execute an HTTP request against the remote API server."""
        ...


def execute_ground_ontology(
    client: GroundingClientPort,
    req: OntologyGroundReq,
) -> GroundingResponse:
    """Execute `opendv ontology ground` via HTTP POST or in-process OpenDVService."""
    if client.is_remote:
        prev_timeout = getattr(client, "timeout_seconds", None)
        bumped = (
            isinstance(prev_timeout, (int, float))
            and float(prev_timeout) < _MIN_GROUNDING_TIMEOUT_SECONDS
        )
        if bumped:
            cast(Any, client).timeout_seconds = _MIN_GROUNDING_TIMEOUT_SECONDS
        try:
            payload = client._http(
                "POST",
                "/api/v1/ontology/ground",
                json_body=req.model_dump(mode="json"),
            ).json()
        finally:
            if bumped:
                cast(Any, client).timeout_seconds = prev_timeout
        return GroundingResponse.model_validate(payload)
    res = client.service().ground_ontology(req)
    if inspect.isawaitable(res):
        return asyncio.run(res)
    return res


def execute_fibo_sync(
    client: GroundingClientPort,
    req: FiboSyncReq,
) -> FiboSyncResp:
    """Execute local-only `opendv ontology fibo-sync` (rejects remote --api-url)."""
    if client.is_remote:
        raise OpenDVError(
            "`opendv ontology fibo-sync` is local-only and does not support "
            "--api-url / OPENDV_API_URL.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return client.service().run_fibo_sync(req)


__all__ = [
    "GroundingClientPort",
    "execute_fibo_sync",
    "execute_ground_ontology",
]
