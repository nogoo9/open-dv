"""Role-based access control (RBAC) normalization and event redaction."""

from __future__ import annotations

from opendv_telemetry import (
    ROLE_CLEARANCE_LEVELS,
    RbacRole,
    redact_event_for_role,
    resolve_cleared_tiers,
)

_ROLE_ALIAS_MAP: dict[str, RbacRole] = {
    "MNPI_CLEARED_STEWARD": RbacRole.AUDITOR_FINANCE,
    "LP_CONFIDENTIAL_STEWARD": RbacRole.DATA_STEWARD,
    "COMPLIANCE_AUDITOR": RbacRole.INTERNAL_MODELER,
}


def normalize_rbac_role(
    role: RbacRole | str | None,
    *,
    default: RbacRole = RbacRole.PUBLIC,
) -> RbacRole:
    """Validate and normalize a 5-Tier RBAC role string or enum value."""
    if isinstance(role, RbacRole):
        return role
    if role is None or str(role).strip() == "":
        return default
    normalized = str(role).strip().upper()
    if normalized in _ROLE_ALIAS_MAP:
        return _ROLE_ALIAS_MAP[normalized]
    try:
        return RbacRole(normalized)
    except ValueError as exc:
        valid_roles = [r.value for r in RbacRole] + sorted(_ROLE_ALIAS_MAP.keys())
        raise ValueError(f"Invalid RBAC role '{role}'. Must be one of {valid_roles}.") from exc


__all__ = [
    "ROLE_CLEARANCE_LEVELS",
    "RbacRole",
    "normalize_rbac_role",
    "redact_event_for_role",
    "resolve_cleared_tiers",
]
