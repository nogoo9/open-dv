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

from pydantic import BaseModel, ValidationError

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


# Post-baseline optional extension field sets subject to scoped empty normalization
# (Task 4A.2 / Issue #198 & ADR-005). Legacy mandatory collections (decision_refs,
# standards_references, canonical_relationships, reference_enumerations, etc.) and
# CBC scalar fields (definition) are preserved when empty so all certified baseline
# SHA-256 digests remain bit-for-bit invariant.
_CBC_EXT: frozenset[str] = frozenset(
    {
        "parent_concept_uri",
        "attributes",
        "synonyms",
        "external_mappings",
        "deprecated",
        "metadata",
        "source_hints",
    }
)
_ATTR_OPT: frozenset[str] = frozenset(
    {
        "description",
        "example_values",
        "deprecated",
        "metadata",
        "source_hints",
    }
)
_REL_EXT: frozenset[str] = frozenset(
    {
        "description",
        "cardinality",
        "deprecated",
        "metadata",
        "source_hints",
    }
)
_ENUM_EXT: frozenset[str] = frozenset(
    {
        "enumeration_name",
        "description",
        "code_descriptions",
        "deprecated",
        "metadata",
    }
)
_ONT_EXT: frozenset[str] = frozenset(
    {
        "description",
        "metadata",
    }
)


def _is_empty_value(v: Any) -> bool:
    """Check if value is None, empty string, or empty collection/mapping."""
    if v is None:
        return True
    return isinstance(v, (str, list, dict)) and len(v) == 0


def _norm_empty(
    obj: Any,
    scope: set[str] | frozenset[str] | None = None,
) -> Any:
    """Scoped empty normalization for Graph A ontology serialization.

    When `scope` is provided:
        Prunes keys in `scope` if their value is empty (None, '', [], {}).
        Recursively applies to nested dicts and lists.

    When `scope` is None (hierarchical ontology mode):
        Applies level-bounded extension scoping across the Graph A ontology hierarchy:
        - Root ontology level: scoped by `_ONT_EXT` (metadata)
        - Canonical Business Concept level: scoped by `_CBC_EXT` (extension fields & annotations)
        - Concept Attribute level: scoped by `_ATTR_OPT` (optional extension annotations)
        - Canonical Relationship level: scoped by `_REL_EXT` (optional extension annotations)
        - Reference Enumeration level: scoped by `_ENUM_EXT` (optional extension annotations)

    Crucial Invariant: DO NOT prune empty legacy collections that were present in baseline models
    (e.g. `governance_constraints: []`, `key_authority_tiers: []`, `decision_refs: []`,
    `standards_references: []`, `canonical_relationships: []`, `reference_enumerations: []`),
    nor legacy scalar fields (e.g. `description: ""`, `definition: ""`, `enumeration_name: ""`).
    """
    if scope is not None:
        if isinstance(obj, dict):
            return {
                k: _norm_empty(v, scope)
                for k, v in obj.items()
                if not (k in scope and _is_empty_value(v))
            }
        if isinstance(obj, list):
            return [_norm_empty(elem, scope) for elem in obj]
        return obj

    # Level-bounded hierarchical scoped normalization when scope is None
    if not isinstance(obj, dict):
        return obj

    # 1. Root ontology level: prune keys in _ONT_EXT if empty
    doc: dict[str, Any] = {
        k: v for k, v in obj.items() if not (k in _ONT_EXT and _is_empty_value(v))
    }

    # 2. Canonical Business Concepts: prune keys in _CBC_EXT and _ATTR_OPT
    if "canonical_business_concepts" in doc and isinstance(
        doc["canonical_business_concepts"], list
    ):
        normalized_cbcs: list[Any] = []
        for cbc in doc["canonical_business_concepts"]:
            if isinstance(cbc, dict):
                c_dict: dict[str, Any] = {
                    k: v for k, v in cbc.items() if not (k in _CBC_EXT and _is_empty_value(v))
                }
                if "attributes" in c_dict and isinstance(c_dict["attributes"], list):
                    normalized_attrs: list[Any] = []
                    for attr in c_dict["attributes"]:
                        if isinstance(attr, dict):
                            normalized_attrs.append(
                                {
                                    k: v
                                    for k, v in attr.items()
                                    if not (k in _ATTR_OPT and _is_empty_value(v))
                                }
                            )
                        else:
                            normalized_attrs.append(attr)
                    c_dict["attributes"] = normalized_attrs
                normalized_cbcs.append(c_dict)
            else:
                normalized_cbcs.append(cbc)
        doc["canonical_business_concepts"] = normalized_cbcs

    # 3. Canonical Relationships: prune keys in _REL_EXT
    if "canonical_relationships" in doc and isinstance(doc["canonical_relationships"], list):
        normalized_rels: list[Any] = []
        for rel in doc["canonical_relationships"]:
            if isinstance(rel, dict):
                normalized_rels.append(
                    {k: v for k, v in rel.items() if not (k in _REL_EXT and _is_empty_value(v))}
                )
            else:
                normalized_rels.append(rel)
        doc["canonical_relationships"] = normalized_rels

    # 4. Reference Enumerations: prune keys in _ENUM_EXT
    if "reference_enumerations" in doc and isinstance(doc["reference_enumerations"], list):
        normalized_enums: list[Any] = []
        for enum in doc["reference_enumerations"]:
            if isinstance(enum, dict):
                normalized_enums.append(
                    {k: v for k, v in enum.items() if not (k in _ENUM_EXT and _is_empty_value(v))}
                )
            else:
                normalized_enums.append(enum)
        doc["reference_enumerations"] = normalized_enums

    return doc


def canonicalize_graph_a_dict(ontology: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    """Convert a GraphAOntology, GenesisDraft, or dict to a deterministically sorted dict."""
    if isinstance(ontology, BaseModel):
        doc = ontology.model_dump(mode="json", exclude_none=True)
    elif isinstance(ontology, Mapping):
        raw_dict = dict(ontology)
        try:
            doc = GraphAOntology.model_validate(raw_dict).model_dump(mode="json", exclude_none=True)
        except (ValidationError, TypeError, ValueError):
            doc = raw_dict
    else:
        doc = dict(ontology)

    # Scoped empty normalization: prune emptied post-baseline extension fields
    doc = _norm_empty(doc, scope=None)

    # Deterministically sort list properties by canonical URNs
    # Note: Do NOT sort concept `attributes` in place, as attribute declaration order is
    # preserved in certified golden vectors (`graph_a_golden.json`).
    if "canonical_business_concepts" in doc and isinstance(
        doc["canonical_business_concepts"], list
    ):
        sorted_cbcs: list[Any] = []
        for cbc in sorted(
            doc["canonical_business_concepts"],
            key=lambda c: c.get("concept_uri", "") if isinstance(c, dict) else "",
        ):
            if isinstance(cbc, dict) and isinstance(cbc.get("external_mappings"), list):
                c_copy = dict(cbc)
                c_copy["external_mappings"] = sorted(
                    c_copy["external_mappings"],
                    key=lambda m: (
                        (
                            m.get("target_uri", ""),
                            m.get("predicate") or "",
                            m.get("scheme", ""),
                        )
                        if isinstance(m, dict)
                        else ("", "", "")
                    ),
                )
                sorted_cbcs.append(c_copy)
            else:
                sorted_cbcs.append(cbc)
        doc["canonical_business_concepts"] = sorted_cbcs
    if "canonical_relationships" in doc and isinstance(doc["canonical_relationships"], list):
        doc["canonical_relationships"] = sorted(
            doc["canonical_relationships"],
            key=lambda r: r.get("relationship_uri", "") if isinstance(r, dict) else "",
        )
    if "reference_enumerations" in doc and isinstance(doc["reference_enumerations"], list):
        doc["reference_enumerations"] = sorted(
            doc["reference_enumerations"],
            key=lambda e: e.get("enum_uri", "") if isinstance(e, dict) else "",
        )
    if "standards_references" in doc and isinstance(doc["standards_references"], list):
        doc["standards_references"] = sorted(doc["standards_references"])
    if "decision_refs" in doc and isinstance(doc["decision_refs"], list):
        doc["decision_refs"] = sorted(doc["decision_refs"])

    return doc


def compute_ontology_sha256(ontology: BaseModel | Mapping[str, Any]) -> str:
    """Compute RFC 8785 JCS canonical SHA-256 hex digest of a GraphAOntology or GenesisDraft."""
    canonical_dict = canonicalize_graph_a_dict(ontology)
    jcs_bytes = dumps_jcs_bytes(canonical_dict)
    return hashlib.sha256(jcs_bytes).hexdigest().lower()


__all__ = [
    "canonicalize_graph_a_dict",
    "compute_ontology_sha256",
    "dumps_jcs_bytes",
]
