"""W3C Distributed Trace Context (trace_id & span_id) propagation for Open-DV.

Conforms to docs/06-explainability-and-observability-spec.md (§1.2)
and W3C Trace Context Level 1 Specification (RFC 7230 / REC-trace-context).
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Final

W3C_TRACE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{32}$")
W3C_SPAN_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{16}$")
W3C_TRACEPARENT_RE: Final[re.Pattern[str]] = re.compile(
    r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$"
)

ZERO_TRACE_ID: Final[str] = "0" * 32
ZERO_SPAN_ID: Final[str] = "0" * 16


def is_valid_trace_id(trace_id: str) -> bool:
    """Validate that trace_id is 32 lowercase hex characters and not all zeros."""
    cleaned = (trace_id or "").strip().lower()
    return bool(W3C_TRACE_ID_RE.match(cleaned)) and cleaned != ZERO_TRACE_ID


def is_valid_span_id(span_id: str) -> bool:
    """Validate that span_id is 16 lowercase hex characters and not all zeros."""
    cleaned = (span_id or "").strip().lower()
    return bool(W3C_SPAN_ID_RE.match(cleaned)) and cleaned != ZERO_SPAN_ID


def generate_trace_id() -> str:
    """Generate a W3C compliant 32-character lowercase hex trace identifier."""
    while True:
        tid = secrets.token_hex(16).lower()
        if tid != ZERO_TRACE_ID:
            return tid


def generate_span_id() -> str:
    """Generate a W3C compliant 16-character lowercase hex span identifier."""
    while True:
        sid = secrets.token_hex(8).lower()
        if sid != ZERO_SPAN_ID:
            return sid


@dataclass(frozen=True)
class TraceContext:
    """Immutable W3C Distributed Trace Context linking multi-agent pipeline executions."""

    trace_id: str
    span_id: str
    parent_span_id: str | None = None

    def __post_init__(self) -> None:
        if not is_valid_trace_id(self.trace_id):
            msg = f"Invalid W3C trace_id: '{self.trace_id}' (must be 32 lowercase hex, non-zero)"
            raise ValueError(msg)
        if not is_valid_span_id(self.span_id):
            msg = f"Invalid W3C span_id: '{self.span_id}' (must be 16 lowercase hex, non-zero)"
            raise ValueError(msg)
        if self.parent_span_id is not None and not is_valid_span_id(self.parent_span_id):
            msg = (
                f"Invalid W3C parent_span_id: '{self.parent_span_id}' "
                "(must be 16 lowercase hex, non-zero)"
            )
            raise ValueError(msg)

    def new_child_span(self) -> TraceContext:
        """Create a child span with this context's span_id as parent_span_id."""
        return TraceContext(
            trace_id=self.trace_id,
            span_id=generate_span_id(),
            parent_span_id=self.span_id,
        )

    def to_w3c_traceparent(self) -> str:
        """Serialize as standard W3C `traceparent` header string (`00-{trace_id}-{span_id}-01`)."""
        return f"00-{self.trace_id}-{self.span_id}-01"

    @classmethod
    def from_w3c_traceparent(cls, header: str) -> TraceContext:
        """Parse standard W3C `traceparent` header string."""
        m = W3C_TRACEPARENT_RE.match(header.strip().lower())
        if not m:
            msg = f"Invalid W3C traceparent header format: '{header}'"
            raise ValueError(msg)
        trace_id, span_id, _flags = m.groups()
        return cls(trace_id=trace_id, span_id=span_id)


_CURRENT_TRACE_CONTEXT: ContextVar[TraceContext | None] = ContextVar(
    "current_trace_context", default=None
)


def get_current_trace_context() -> TraceContext | None:
    """Retrieve the active TraceContext for the current execution fiber."""
    return _CURRENT_TRACE_CONTEXT.get()


def set_current_trace_context(ctx: TraceContext) -> Token[TraceContext | None]:
    """Set the active TraceContext for the current execution fiber."""
    return _CURRENT_TRACE_CONTEXT.set(ctx)


def reset_current_trace_context(token: Token[TraceContext | None]) -> None:
    """Reset the TraceContext back to its previous state."""
    _CURRENT_TRACE_CONTEXT.reset(token)


@contextmanager
def trace_scope(ctx: TraceContext | None = None) -> Iterator[TraceContext]:
    """Context manager scoping a TraceContext across sync or async operations.

    If `ctx` is not provided, inherits the current context and spawns a child span,
    or creates a new root TraceContext if none is currently active.
    """
    if ctx is None:
        current = get_current_trace_context()
        if current is not None:
            active_ctx = current.new_child_span()
        else:
            active_ctx = TraceContext(
                trace_id=generate_trace_id(),
                span_id=generate_span_id(),
            )
    else:
        active_ctx = ctx

    token = set_current_trace_context(active_ctx)
    try:
        yield active_ctx
    finally:
        reset_current_trace_context(token)


__all__ = [
    "TraceContext",
    "generate_span_id",
    "generate_trace_id",
    "get_current_trace_context",
    "is_valid_span_id",
    "is_valid_trace_id",
    "reset_current_trace_context",
    "set_current_trace_context",
    "trace_scope",
]
