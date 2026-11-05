"""RFC 8785 canonical JSON printer for spec-driven CLI outputs."""

from __future__ import annotations

import sys
from typing import Any, TextIO

from opendv_contracts.jcs import dumps_jcs


def _to_serializable(obj: Any) -> Any:
    """Normalize object into a JSON-canonicalizable Python primitive structure."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict

        return asdict(obj)
    if isinstance(obj, dict):
        return {str(k): _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_serializable(x) for x in obj]
    return obj


def format_jcs(payload: Any) -> str:
    """Serialize payload to deterministic RFC 8785 canonical JSON."""
    normalized = _to_serializable(payload)
    return dumps_jcs(normalized)


def print_jcs(payload: Any, *, file: TextIO | None = None) -> None:
    """Print payload as deterministic RFC 8785 canonical JSON to target file stream."""
    target = file if file is not None else sys.stdout
    canonical_str = format_jcs(payload)
    print(canonical_str, file=target)


__all__ = ["format_jcs", "print_jcs"]
