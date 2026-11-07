"""RFC 8785 JSON Canonicalization Scheme (JCS) serializer.

Conforms to RFC 8785 and ECMAScript 7.1.12.1 number serialization:
- Deterministic UTF-16 code unit key sorting
- Zero whitespace outside string literals
- ECMAScript number serialization (e.g. 1.0 -> 1, 12.80 -> 12.8, -0.0 -> 0)
- Control character escaping without escaping non-control Unicode
"""

from __future__ import annotations

import io
import json
import math
import re
from collections.abc import Mapping
from typing import Any

# Safe integer range per RFC 8785 Appendix B, Note 1: [-(2^53 - 1), 2^53 - 1]
_INT_MAX = 2**53 - 1
_INT_MIN = -(2**53) + 1

_ESCAPE = re.compile(r'[\x00-\x1f\\"\b\f\n\r\t]')
_ESCAPE_MAP = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}
for i in range(0x20):
    _ESCAPE_MAP.setdefault(chr(i), f"\\u{i:04x}")


class CanonicalizationError(ValueError):
    """Base error for all errors during canonicalization."""

    pass


class IntegerDomainError(CanonicalizationError):
    """Raised when an integer exceeds the safe IEEE 754 double precision integer range."""

    def __init__(self, n: int) -> None:
        super().__init__(f"Integer {n} exceeds safe IEEE 754 float domain [-(2^53-1), 2^53-1]")


class FloatDomainError(CanonicalizationError):
    """Raised when a float cannot be represented in JCS (NaN or Infinity)."""

    def __init__(self, f: float) -> None:
        super().__init__(
            f"Float {f} is not representable in RFC 8785 JCS (NaN/Infinity prohibited)"
        )


def _serialize_str(s: str, sink: io.BytesIO) -> None:
    def _replace(match: re.Match[str]) -> str:
        return _ESCAPE_MAP[match.group(0)]

    sink.write(b'"')
    try:
        sink.write(_ESCAPE.sub(_replace, s).encode("utf-8"))
    except UnicodeEncodeError as e:
        raise CanonicalizationError("String contains invalid UTF-8 codepoints") from e
    sink.write(b'"')


def _serialize_float(f: float, sink: io.BytesIO) -> None:
    """Serialize a float according to ECMAScript 7.1.12.1 and RFC 8785 §3.2.2.3."""
    if math.isnan(f) or math.isinf(f):
        raise FloatDomainError(f)

    # -0.0 and +0.0 both serialize to "0" (minus sign prohibited for negative zero)
    if f == 0:
        sink.write(b"0")
        return

    # Negative numbers: write "-" and recurse on absolute value
    if f < 0:
        sink.write(b"-")
        _serialize_float(-f, sink)
        return

    # Non-zero positive float:
    stringified = str(f)
    exponent_str = ""
    exponent_value = 0
    q = stringified.find("e")
    if q > 0:
        exponent_str = stringified[q:]
        # Suppress leading zero on exponents (e.g. e-05 -> e-5)
        if len(exponent_str) > 2 and exponent_str[2] == "0":
            exponent_str = exponent_str[:2] + exponent_str[3:]
        stringified = stringified[:q]
        exponent_value = int(exponent_str[1:])

    first = stringified
    dot = ""
    last = ""
    q = stringified.find(".")
    if q > 0:
        dot = "."
        first = stringified[:q]
        last = stringified[q + 1 :]

    if last == "0":
        # Strip trailing .0
        dot = ""
        last = ""

    if 0 < exponent_value < 21:
        # Integers under 10^21 serialize in standard integer notation
        first += last
        last = ""
        dot = ""
        exponent_str = ""
        q = exponent_value - len(first)
        while q >= 0:
            q -= 1
            first += "0"
    elif -7 < exponent_value < 0:
        # Values >= 1e-6 serialize in decimal notation: 0.000...
        last = first + last
        first = "0"
        dot = "."
        exponent_str = ""
        q = exponent_value
        while q < -1:
            q += 1
            last = "0" + last

    sink.write(f"{first}{dot}{last}{exponent_str}".encode())


def _dump(obj: Any, sink: io.BytesIO) -> None:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(mode="json")

    if obj is None:
        sink.write(b"null")
    elif isinstance(obj, bool):
        sink.write(b"true" if obj else b"false")
    elif isinstance(obj, int):
        if obj < _INT_MIN or obj > _INT_MAX:
            raise IntegerDomainError(obj)
        sink.write(str(obj).encode("utf-8"))
    elif isinstance(obj, float):
        _serialize_float(obj, sink)
    elif isinstance(obj, str):
        _serialize_str(obj, sink)
    elif isinstance(obj, (list, tuple)):
        if not obj:
            sink.write(b"[]")
            return
        sink.write(b"[")
        for idx, elem in enumerate(obj):
            if idx > 0:
                sink.write(b",")
            _dump(elem, sink)
        sink.write(b"]")
    elif isinstance(obj, Mapping):
        if not obj:
            sink.write(b"{}")
            return
        try:
            # RFC 8785 §3.2.3: keys ordered by UTF-16 code units (big-endian byte comparison)
            sorted_items = sorted(obj.items(), key=lambda kv: str(kv[0]).encode("utf-16be"))
        except AttributeError as e:
            raise CanonicalizationError("Object keys must be strings") from e

        sink.write(b"{")
        for idx, (k, v) in enumerate(sorted_items):
            if idx > 0:
                sink.write(b",")
            _serialize_str(str(k), sink)
            sink.write(b":")
            _dump(v, sink)
        sink.write(b"}")
    else:
        raise CanonicalizationError(f"Unsupported type for canonicalization: {type(obj).__name__}")


def dumps_jcs_bytes(obj: Any) -> bytes:
    """Serialize an object into RFC 8785 canonical JSON UTF-8 bytes."""
    sink = io.BytesIO()
    _dump(obj, sink)
    return sink.getvalue()


def dumps_jcs(obj: Any) -> str:
    """Serialize an object into an RFC 8785 canonical JSON string."""
    return dumps_jcs_bytes(obj).decode("utf-8")


def loads_jcs(s: str | bytes) -> Any:
    """Parse a canonical or regular JSON payload into Python objects."""
    return json.loads(s)
