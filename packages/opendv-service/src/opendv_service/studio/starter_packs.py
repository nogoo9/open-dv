"""Wizard v2 starter pack helpers delegating to AssetManagementStarterPackRegistry."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from opendv_contracts.dtos.starter_packs import AssetManagementPackId, StarterPackMetadata
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_sources.asset_management.loader import AssetManagementStarterPackRegistry


def list_wizard_starter_packs(
    registry: AssetManagementStarterPackRegistry | None = None,
) -> list[StarterPackMetadata]:
    """Return metadata summaries for all registered Asset Management starter packs."""
    reg = registry or AssetManagementStarterPackRegistry()
    return list(reg.list_packs())


def create_session_from_starter_pack(
    session_manager: Any,
    *,
    session_id: str,
    pack_id: str | AssetManagementPackId | Sequence[str | AssetManagementPackId],
    domain_namespace: str | None = None,
    enterprise_owner: str | None = None,
    version: str = "1.0.0",
    registry: AssetManagementStarterPackRegistry | None = None,
) -> Any:
    """Create a new Wizard v2 Studio session seeded from one or more AM starter packs."""
    reg = registry or AssetManagementStarterPackRegistry()
    if isinstance(pack_id, (list, tuple, set)):
        pack_list = list(pack_id)
        if not pack_list:
            raise OpenDVError(
                "At least one starter pack_id is required.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        ont = reg.compose_packs(
            pack_list,
            version=version,
            domain_namespace=domain_namespace or "asset_management",
        )
    else:
        try:
            ont = reg.load_pack(pack_id)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            raise OpenDVError(
                f"Unknown starter pack '{pack_id}': {exc}",
                error_code=ErrorCode.UNKNOWN_REFERENCE_PACK,
            ) from exc
        if domain_namespace:
            ont = ont.model_copy(update={"domain_namespace": domain_namespace})

    eff_ns = domain_namespace or ont.domain_namespace
    return session_manager.create_session(
        session_id=session_id,
        domain_namespace=eff_ns,
        enterprise_owner=enterprise_owner or ont.enterprise_owner,
        initial_ontology=ont,
    )


__all__ = [
    "create_session_from_starter_pack",
    "list_wizard_starter_packs",
]
