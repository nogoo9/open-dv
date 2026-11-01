"""Open-DV Telemetry: Immutable Event Ledger, CAS Store, and 5-Tier RBAC Redaction.

Conforms to docs/06-explainability-and-observability-spec.md
and docs/08-platform-mvp-monorepo-and-api-spec.md (§4.2, §5.2).
"""

from __future__ import annotations

from opendv_telemetry.cas_store import (
    ArtifactCorruptedError,
    ArtifactNotFoundError,
    ContentAddressableStore,
    compute_cas_sha256,
)
from opendv_telemetry.hash_pills import (
    format_compact_hash_pill,
    format_error_key_pill,
    format_ghost_pill,
    format_hash_diff_pill,
    format_hub_pill,
    format_link_pill,
    format_semantic_hash_pill,
    format_zero_key_pill,
)
from opendv_telemetry.ledger_emitter import (
    VaultLedgerEmitter,
    format_sse_event_stream,
)
from opendv_telemetry.pii_detector import (
    LeakFinding,
    PIILeakViolationError,
    compute_shannon_entropy_text,
    detect_unmasked_pii_leaks,
    scan_event_for_leaks,
    validate_no_pii_leaks,
)
from opendv_telemetry.rbac_redactor import (
    ROLE_CLEARANCE_LEVELS,
    RbacRole,
    redact_event_for_role,
    redact_telemetry_event,
    resolve_cleared_tiers,
)
from opendv_telemetry.trace_context import (
    TraceContext,
    generate_span_id,
    generate_trace_id,
    get_current_trace_context,
    is_valid_span_id,
    is_valid_trace_id,
    reset_current_trace_context,
    set_current_trace_context,
    trace_scope,
)

__version__ = "0.1.0"

__all__ = [
    "ROLE_CLEARANCE_LEVELS",
    "ArtifactCorruptedError",
    "ArtifactNotFoundError",
    "ContentAddressableStore",
    "LeakFinding",
    "PIILeakViolationError",
    "RbacRole",
    "TraceContext",
    "VaultLedgerEmitter",
    "__version__",
    "compute_cas_sha256",
    "compute_shannon_entropy_text",
    "detect_unmasked_pii_leaks",
    "format_compact_hash_pill",
    "format_error_key_pill",
    "format_ghost_pill",
    "format_hash_diff_pill",
    "format_hub_pill",
    "format_link_pill",
    "format_semantic_hash_pill",
    "format_sse_event_stream",
    "format_zero_key_pill",
    "generate_span_id",
    "generate_trace_id",
    "get_current_trace_context",
    "is_valid_span_id",
    "is_valid_trace_id",
    "redact_event_for_role",
    "redact_telemetry_event",
    "reset_current_trace_context",
    "resolve_cleared_tiers",
    "scan_event_for_leaks",
    "set_current_trace_context",
    "trace_scope",
    "validate_no_pii_leaks",
]
