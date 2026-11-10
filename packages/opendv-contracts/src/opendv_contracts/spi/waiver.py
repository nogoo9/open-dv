"""Steward waiver verification SPI protocol."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from opendv_contracts.dtos.ontology import StewardWaiverRecord


@runtime_checkable
class WaiverVerifier(Protocol):
    """SPI protocol for verifying cryptographic steward waiver records."""

    def verify_waiver(
        self,
        waiver: StewardWaiverRecord,
        delta_sha256: str,
        base_sha256: str,
        **kwargs: Any,
    ) -> bool:
        """Verify validity, authorization, and cryptographic integrity of a steward waiver."""
        ...
