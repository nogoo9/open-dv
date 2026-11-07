"""Cryptographic hashing and canonical serialization for Graph A ontologies.

Conforms to RFC 8785 JSON Canonicalization Scheme (JCS) and strict architectural layering:
packages/opendv-core must not import from higher layers (e.g. opendv_contracts).
"""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from opendv_core.models.graph_a import GraphAOntology

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
for _i in range(0x20):
    _ESCAPE_MAP.setdefault(chr(_i), f"\\u{_i:04x}")


def _serialize_str(s: str, sink: io.BytesIO) -> None:
    def _replace(match: re.Match[str]) -> str:
        return _ESCAPE_MAP[match.group(0)]

    sink.write(b'"')
    sink.write(_ESCAPE.sub(_replace, s).encode("utf-8"))
    sink.write(b'"')


def _serialize_float(f: float, sink: io.BytesIO) -> None:
    """Serialize a float according to ECMAScript 7.1.12.1 and RFC 8785 §3.2.2.3."""
    if math.isnan(f) or math.isinf(f):
        raise ValueError(f"Float {f} is not representable in RFC 8785 JCS")

    if f == 0:
        sink.write(b"0")
        return

    if f < 0:
        sink.write(b"-")
        _serialize_float(-f, sink)
        return

    stringified = str(f)
    exponent_str = ""
    exponent_value = 0
    q = stringified.find("e")
    if q > 0:
        exponent_str = stringified[q:]
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
        dot = ""
        last = ""

    if 0 < exponent_value < 21:
        first += last
        last = ""
        dot = ""
        exponent_str = ""
        q = exponent_value - len(first)
        while q >= 0:
            q -= 1
            first += "0"
    elif -7 < exponent_value < 0:
        last = first + last
        first = "0"
        dot = "."
        exponent_str = ""
        q = exponent_value
        while q < -1:
            q += 1
            last = "0" + last

    sink.write(f"{first}{dot}{last}{exponent_str}".encode())


def _dump_jcs(obj: Any, sink: io.BytesIO) -> None:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(mode="json")

    if obj is None:
        sink.write(b"null")
    elif isinstance(obj, bool):
        sink.write(b"true" if obj else b"false")
    elif isinstance(obj, int):
        if obj < _INT_MIN or obj > _INT_MAX:
            raise ValueError(f"Integer {obj} exceeds safe IEEE 754 domain")
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
            _dump_jcs(elem, sink)
        sink.write(b"]")
    elif isinstance(obj, Mapping):
        if not obj:
            sink.write(b"{}")
            return
        sorted_items = sorted(obj.items(), key=lambda kv: str(kv[0]).encode("utf-16be"))
        sink.write(b"{")
        for idx, (k, v) in enumerate(sorted_items):
            if idx > 0:
                sink.write(b",")
            _serialize_str(str(k), sink)
            sink.write(b":")
            _dump_jcs(v, sink)
        sink.write(b"}")
    else:
        raise ValueError(f"Unsupported type for JCS canonicalization: {type(obj).__name__}")


def dumps_jcs_bytes(obj: Any) -> bytes:
    """Serialize an object into RFC 8785 canonical JSON UTF-8 bytes."""
    sink = io.BytesIO()
    _dump_jcs(obj, sink)
    return sink.getvalue()


def canonicalize_graph_a_dict(ontology: GraphAOntology | dict[str, Any]) -> dict[str, Any]:
    """Convert a GraphAOntology or dict to a deterministically sorted dictionary representation."""
    if isinstance(ontology, GraphAOntology):
        doc = ontology.model_dump(mode="json", exclude_none=True)
    elif isinstance(ontology, dict):
        try:
            doc = GraphAOntology.model_validate(ontology).model_dump(mode="json", exclude_none=True)
        except (ValidationError, TypeError, ValueError):
            doc = dict(ontology)
    else:
        doc = dict(ontology)

    # Deterministically sort list properties by canonical URNs
    if "canonical_business_concepts" in doc and isinstance(
        doc["canonical_business_concepts"], list
    ):
        doc["canonical_business_concepts"] = sorted(
            doc["canonical_business_concepts"],
            key=lambda c: c.get("concept_uri", ""),
        )
    if "canonical_relationships" in doc and isinstance(doc["canonical_relationships"], list):
        doc["canonical_relationships"] = sorted(
            doc["canonical_relationships"],
            key=lambda r: r.get("relationship_uri", ""),
        )
    if "reference_enumerations" in doc and isinstance(doc["reference_enumerations"], list):
        doc["reference_enumerations"] = sorted(
            doc["reference_enumerations"],
            key=lambda e: e.get("enum_uri", ""),
        )
    if "standards_references" in doc and isinstance(doc["standards_references"], list):
        doc["standards_references"] = sorted(doc["standards_references"])
    if "decision_refs" in doc and isinstance(doc["decision_refs"], list):
        doc["decision_refs"] = sorted(doc["decision_refs"])

    return doc


def compute_ontology_sha256(ontology: GraphAOntology | dict[str, Any]) -> str:
    """Compute RFC 8785 JCS canonical SHA-256 hex digest of a GraphAOntology."""
    canonical_dict = canonicalize_graph_a_dict(ontology)
    jcs_bytes = dumps_jcs_bytes(canonical_dict)
    return hashlib.sha256(jcs_bytes).hexdigest().lower()


__all__ = [
    "canonicalize_graph_a_dict",
    "compute_ontology_sha256",
    "dumps_jcs_bytes",
]
