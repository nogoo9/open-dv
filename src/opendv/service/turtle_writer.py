"""Pure-Python W3C Turtle 1.1 writer for V2 GraphAOntology (zero rdflib)."""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Mapping
from typing import Any, Final

from pydantic import ValidationError

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.ontology import GraphAOntology
from opendv.models.vault_ir import DecimalType, PortableLogicalType

_DV2_NS: Final[str] = "https://open-dv.dev/ontology/core#"
_OWL_NS: Final[str] = "http://www.w3.org/2002/07/owl#"
_RDF_NS: Final[str] = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_RDFS_NS: Final[str] = "http://www.w3.org/2000/01/rdf-schema#"
_SKOS_NS: Final[str] = "http://www.w3.org/2004/02/skos/core#"
_XSD_NS: Final[str] = "http://www.w3.org/2001/XMLSchema#"

_RFC3987_IRI_RE: Final[re.Pattern[str]] = re.compile(
    r"\A[A-Za-z][A-Za-z0-9+.-]*:[^\s<>\"{}|\\^`\x00-\x1f\x7f-\x9f]+\Z"
)

_ANNOTATION_PROPS: Final[tuple[str, ...]] = (
    "dv2:domainNamespace",
    "dv2:keyAuthorityTier",
    "dv2:bkccDefault",
    "dv2:governanceTag",
    "dv2:isOptional",
    "dv2:isBusinessKey",
    "dv2:isPii",
    "dv2:rawDataType",
    "dv2:hasMappingDetail",
    "dv2:mappingTarget",
    "dv2:mappingReferencePack",
    "dv2:mappingPackSha256",
    "dv2:mappingConfidence",
    "dv2:hasSynonymDetail",
    "dv2:orderIndex",
    "dv2:targetLink",
    "dv2:cardinality",
    "dv2:relationshipDescription",
    "dv2:targetReferenceTable",
    "dv2:allowedCode",
    "dv2:hasCodeDetail",
    "dv2:codeValue",
    "skos:prefLabel",
    "skos:altLabel",
    "skos:definition",
    "skos:exactMatch",
    "skos:closeMatch",
)


def _safe_iri(iri: str, *, field_name: str) -> str:
    if not isinstance(iri, str) or not iri.strip():
        raise OpenDVError(
            f"Empty {field_name} cannot be serialized as a Turtle IRI.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    cleaned = iri.strip()
    if not _RFC3987_IRI_RE.match(cleaned):
        raise OpenDVError(
            f"Malformed {field_name} '{iri}' violates RFC 3987 IRI syntax.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    parts = urllib.parse.urlsplit(cleaned)
    if parts.scheme.lower() in ("http", "https") and not parts.netloc:
        raise OpenDVError(
            f"HTTP(S) {field_name} '{iri}' is missing host authority.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return f"<{cleaned}>"


def _escape_ttl_str(value: str) -> str:
    out: list[str] = ['"']
    for ch in value:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif code < 0x20 or code == 0x7F:
            out.append(f"\\u{code:04X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _lit_str(value: str) -> str:
    return _escape_ttl_str(value)


def _lit_bool(value: bool) -> str:
    return "true" if value else "false"


def _lit_int(value: int) -> str:
    return str(value)


def _lit_decimal(value: str) -> str:
    return f"{_escape_ttl_str(value)}^^xsd:decimal"


def _xsd_for_logical_type(ltype: PortableLogicalType) -> tuple[str, str]:
    if isinstance(ltype, DecimalType):
        return "xsd:decimal", f"DECIMAL({ltype.precision},{ltype.scale})"
    if not isinstance(ltype, str):
        return "xsd:string", ltype.kind
    mapping: dict[str, str] = {
        "UTF8_STRING": "xsd:string",
        "INT32": "xsd:integer",
        "INT64": "xsd:integer",
        "DATE32": "xsd:date",
        "TIMESTAMP_UTC_US": "xsd:dateTime",
        "BOOLEAN": "xsd:boolean",
        "FIXED_BINARY_32": "xsd:hexBinary",
        "JSON_DOCUMENT": "xsd:string",
    }
    return mapping.get(ltype, "xsd:string"), ltype


def _coerce_ontology(ontology: GraphAOntology | Mapping[str, Any]) -> GraphAOntology:
    if isinstance(ontology, GraphAOntology):
        return ontology
    try:
        return GraphAOntology.model_validate(dict(ontology))
    except (ValidationError, TypeError, ValueError) as exc:
        raise OpenDVError(
            f"Ontology payload failed schema validation for Turtle export: {exc}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from exc


def write_ontology_turtle(ontology: GraphAOntology | Mapping[str, Any]) -> str:
    """Serialize a V2 GraphAOntology deterministically into W3C Turtle 1.1."""
    ont = _coerce_ontology(ontology)
    lines: list[str] = [
        f"@prefix dv2: <{_DV2_NS}> .",
        f"@prefix owl: <{_OWL_NS}> .",
        f"@prefix rdf: <{_RDF_NS}> .",
        f"@prefix rdfs: <{_RDFS_NS}> .",
        f"@prefix skos: <{_SKOS_NS}> .",
        f"@prefix xsd: <{_XSD_NS}> .",
        "",
    ]

    for prop in _ANNOTATION_PROPS:
        lines.append(f"{prop} a owl:AnnotationProperty .")
    lines.append("")

    ont_iri = _safe_iri(
        f"https://open-dv.dev/ontology/{ont.domain_namespace}",
        field_name="domain_namespace",
    )
    ver_iri = _safe_iri(
        f"https://open-dv.dev/ontology/{ont.domain_namespace}/v{ont.ontology_version}",
        field_name="ontology_version",
    )
    lines.extend(
        [
            f"{ont_iri} a owl:Ontology ;",
            f"    owl:versionInfo {_lit_str(ont.ontology_version)} ;",
            f"    owl:versionIRI {ver_iri} ;",
            f"    dv2:domainNamespace {_lit_str(ont.domain_namespace)} .",
            "",
        ]
    )

    for concept in ont.concepts:
        c_iri = _safe_iri(concept.concept_uri, field_name="concept_uri")
        c_raw = concept.concept_uri.strip()
        sep = "/" if "#" in c_raw else "#"

        lines.append(f"{c_iri} a owl:Class ;")
        lines.append(f"    rdfs:label {_lit_str(concept.concept_name)} ;")
        lines.append(f"    skos:prefLabel {_lit_str(concept.concept_name)} ;")
        if concept.description:
            lines.append(f"    rdfs:comment {_lit_str(concept.description)} ;")
            lines.append(f"    skos:definition {_lit_str(concept.description)} ;")
        if concept.parent_concept_uri:
            p_iri = _safe_iri(concept.parent_concept_uri, field_name="parent_concept_uri")
            lines.append(f"    rdfs:subClassOf {p_iri} ;")
        lines.append(f"    dv2:keyAuthorityTier {_lit_str(concept.key_authority_tier)} ;")
        if concept.bkcc_default:
            lines.append(f"    dv2:bkccDefault {_lit_str(concept.bkcc_default)} ;")
        for tag in concept.governance_tags:
            lines.append(f"    dv2:governanceTag {_lit_str(tag)} ;")
        for syn in concept.synonyms:
            lines.append(f"    skos:altLabel {_lit_str(syn)} ;")

        syn_blocks: list[list[str]] = []
        for idx, syn in enumerate(concept.synonyms):
            syn_iri = _safe_iri(f"{c_raw}{sep}synonym_{idx}", field_name="synonym.detail_uri")
            lines.append(f"    dv2:hasSynonymDetail {syn_iri} ;")
            syn_blocks.append(
                [
                    f"{syn_iri} dv2:orderIndex {_lit_int(idx)} ;",
                    f"    skos:altLabel {_lit_str(syn)} .",
                ]
            )

        map_blocks: list[list[str]] = []
        for idx, mapping in enumerate(concept.external_mappings):
            target_iri = _safe_iri(
                mapping.standard_uri, field_name="external_mapping.standard_uri"
            )
            pred = (
                "skos:exactMatch"
                if mapping.mapping_type == "skos:exactMatch"
                else "skos:closeMatch"
            )
            lines.append(f"    {pred} {target_iri} ;")
            map_iri = _safe_iri(f"{c_raw}{sep}mapping_{idx}", field_name="external_mapping.uri")
            lines.append(f"    dv2:hasMappingDetail {map_iri} ;")
            map_blocks.append(
                [
                    f"{map_iri} dv2:mappingTarget {_lit_str(mapping.standard_uri)} ;",
                    f"    dv2:mappingReferencePack {_lit_str(mapping.reference_pack_id)} ;",
                    f"    dv2:mappingPackSha256 {_lit_str(mapping.reference_pack_sha256)} ;",
                    f"    dv2:mappingConfidence {_lit_decimal(format(mapping.confidence, 'f'))} .",
                ]
            )

        # Terminate class block cleanly on last triple
        lines[-1] = lines[-1][:-1] + "."
        lines.append("")

        for blk in syn_blocks:
            lines.extend(blk)
        for blk in map_blocks:
            lines.extend(blk)
        if syn_blocks or map_blocks:
            lines.append("")

        for idx, attr in enumerate(concept.attributes):
            clean_attr = urllib.parse.quote(attr.name.strip(), safe="")
            attr_iri = _safe_iri(f"{c_raw}{sep}{clean_attr}", field_name="attribute_uri")
            xsd_range, raw_ltype = _xsd_for_logical_type(attr.logical_type)
            lines.extend(
                [
                    f"{attr_iri} a owl:DatatypeProperty ;",
                    f"    rdfs:domain {c_iri} ;",
                    f"    rdfs:range {xsd_range} ;",
                    f"    rdfs:label {_lit_str(attr.name)} ;",
                    f"    dv2:orderIndex {_lit_int(idx)} ;",
                    f"    dv2:rawDataType {_lit_str(raw_ltype)} ;",
                    f"    dv2:isOptional {_lit_bool(attr.nullable)} ;",
                    f"    dv2:isBusinessKey {_lit_bool(attr.is_business_key)} ;",
                    f"    dv2:isPii {_lit_bool(attr.is_pii)} .",
                    "",
                ]
            )

    for rel in ont.relationships:
        rel_iri = _safe_iri(rel.relationship_uri, field_name="relationship_uri")
        src_iri = _safe_iri(rel.source_concept_uri, field_name="relationship.source_concept_uri")
        tgt_iri = _safe_iri(rel.target_concept_uri, field_name="relationship.target_concept_uri")
        lines.extend(
            [
                f"{rel_iri} a owl:ObjectProperty ;",
                f"    rdfs:label {_lit_str(rel.relationship_name)} ;",
                f"    rdfs:domain {src_iri} ;",
                f"    rdfs:range {tgt_iri} ;",
                f"    dv2:targetLink {_lit_str(rel.target_link)} ;",
                f"    dv2:cardinality {_lit_str(rel.cardinality)} ;",
                f"    rdfs:comment {_lit_str(rel.grain_semantics)} ;",
                f"    dv2:relationshipDescription {_lit_str(rel.description)} .",
                "",
            ]
        )

    for enum in ont.enumerations:
        enum_iri = _safe_iri(enum.enum_uri, field_name="enum_uri")
        e_raw = enum.enum_uri.strip()
        e_sep = "/" if "#" in e_raw else "#"
        lines.extend(
            [
                f"{enum_iri} a rdfs:Datatype ;",
                f"    rdfs:label {_lit_str(enum.enumeration_name)} ;",
                f"    rdfs:comment {_lit_str(enum.description)} ;",
                f"    dv2:targetReferenceTable {_lit_str(enum.target_reference_table)} ;",
            ]
        )
        code_blocks: list[list[str]] = []
        for idx, (code_key, code_desc) in enumerate(sorted(enum.codes.items())):
            lines.append(f"    dv2:allowedCode {_lit_str(code_key)} ;")
            code_iri = _safe_iri(f"{e_raw}{e_sep}code_{idx}", field_name="enum.code_detail")
            lines.append(f"    dv2:hasCodeDetail {code_iri} ;")
            code_blocks.append(
                [
                    f"{code_iri} dv2:orderIndex {_lit_int(idx)} ;",
                    f"    dv2:codeValue {_lit_str(code_key)} ;",
                    f"    rdfs:comment {_lit_str(code_desc)} .",
                ]
            )
        lines[-1] = lines[-1][:-1] + "."
        lines.append("")
        for blk in code_blocks:
            lines.extend(blk)
        if code_blocks:
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def export_ontology_turtle(ontology: GraphAOntology | Mapping[str, Any]) -> tuple[str, str]:
    """Return (turtle_text, 'text/turtle') for a V2 GraphAOntology."""
    return write_ontology_turtle(ontology), "text/turtle"


__all__ = [
    "export_ontology_turtle",
    "write_ontology_turtle",
]
